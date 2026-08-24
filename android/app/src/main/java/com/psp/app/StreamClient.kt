package com.psp.app

import android.util.Log
import org.json.JSONObject
import java.io.InputStream
import java.io.OutputStream
import java.net.InetSocketAddress
import java.net.Socket
import java.nio.ByteBuffer
import java.nio.ByteOrder

/**
 * Manages the TCP connection to the PSP host.
 *
 * Handles:
 * - Connection establishment
 * - Protocol handshake (hello/welcome)
 * - Receiving video frames (passed to DecoderThread)
 * - Sending input events (via InputSender)
 * - Ping/pong for latency measurement
 */
class StreamClient(
    private val host: String,
    private val port: Int,
    private val settings: ConnectionSettings,
    private val callback: ClientCallback
) {
    companion object {
        private const val TAG = "StreamClient"
        private const val FLAG_KEYFRAME = 0x01
        private const val FLAG_CONFIG = 0x02
        private const val FLAG_CONTROL = 0x80
    }

    interface ClientCallback {
        fun onConnected(params: SessionParams)
        fun onVideoFrame(data: ByteArray, isKeyframe: Boolean, isConfig: Boolean)
        fun onControlMessage(json: JSONObject)
        fun onDisconnected(reason: String)
        fun onLatencyMeasured(ms: Long)
        fun onBitrateChanged(bitrateKbps: Int)
    }

    data class SessionParams(
        val codec: String,
        val width: Int,
        val height: Int,
        val fps: Int,
        val bitrateKbps: Int,
        val virtualWidth: Int,
        val virtualHeight: Int
    )

    data class ConnectionSettings(
        val width: Int = 1920,
        val height: Int = 1080,
        val fps: Int = 60,
        val bitrateKbps: Int = 10000,
        val codec: String = "h264",
        val screenWidth: Int = 0,
        val screenHeight: Int = 0
    )

    private var socket: Socket? = null
    private var inputStream: InputStream? = null
    private var outputStream: OutputStream? = null
    var inputSender: InputSender? = null
        private set
    private var running = false
    private var pingId = 0
    private var lastPingTime = 0L
    private var totalBytesReceived = 0L
    private var lastBitrateTime = 0L
    private var bitrateBytes = 0L

    fun start() {
        Thread({ doConnect() }, "StreamClient").start()
    }

    fun stop() {
        running = false
        try {
            socket?.close()
        } catch (_: Exception) {}
    }

    private fun doConnect() {
        try {
            Log.i(TAG, "Connecting to $host:$port ...")

            val sock = Socket()
            sock.connect(InetSocketAddress(host, port), 5000)  // 5s timeout
            sock.tcpNoDelay = true
            sock.soTimeout = 30000  // 30s read timeout (first frame may take a while)
            socket = sock
            inputStream = sock.getInputStream()
            outputStream = sock.getOutputStream()
            inputSender = InputSender(outputStream!!)

            // Handshake: send hello
            if (!doHandshake()) {
                callback.onDisconnected("握手失败")
                return
            }

            running = true
            callback.onConnected(sessionParams!!)

            // Read loop
            readLoop()

        } catch (e: Exception) {
            Log.e(TAG, "Connection error: ${e.message}")
            callback.onDisconnected(e.message ?: "连接失败")
        } finally {
            running = false
            try {
                socket?.close()
            } catch (_: Exception) {}
        }
    }

    private var sessionParams: SessionParams? = null

    private fun doHandshake(): Boolean {
        val hello = JSONObject().apply {
            put("type", "hello")
            put("proto", 1)
            put("device", android.os.Build.MODEL)
            put("screen_width", settings.screenWidth)
            put("screen_height", settings.screenHeight)
            put("display_mode", 0)  // 0=镜像, 1=扩展
            put("use_hardware_encoder", false)  // false=软件, true=硬件
            put("want", JSONObject().apply {
                put("codec", settings.codec)
                put("width", settings.width)
                put("height", settings.height)
                put("fps", settings.fps)
                put("bitrate_kbps", settings.bitrateKbps)
                put("display_mode", 0)
                put("use_hardware_encoder", false)
            })
            put("input", true)
        }

        // Send hello as control frame
        sendControlFrame(hello)

        // Read welcome response
        val (flags, payload) = readFrame()
        if (flags == -1) return false

        if (flags and FLAG_CONTROL == 0) {
            Log.e(TAG, "Expected control frame for welcome")
            return false
        }

        val welcome = JSONObject(String(payload, Charsets.UTF_8))
        if (!welcome.optBoolean("ok", false)) {
            Log.e(TAG, "Server rejected: ${welcome.optString("reason")}")
            return false
        }

        sessionParams = SessionParams(
            codec = welcome.getString("codec"),
            width = welcome.getInt("width"),
            height = welcome.getInt("height"),
            fps = welcome.getInt("fps"),
            bitrateKbps = welcome.getInt("bitrate_kbps"),
            virtualWidth = welcome.optInt("virtual_display_width", settings.width),
            virtualHeight = welcome.optInt("virtual_display_height", settings.height)
        )

        Log.i(TAG, "Handshake OK: ${sessionParams}")
        return true
    }

    private fun readLoop() {
        val headerBuf = ByteArray(4)
        while (running) {
            try {
                // Read 4-byte header
                readExact(inputStream!!, headerBuf, 4)
                val bodyLen = ByteBuffer.wrap(headerBuf).order(ByteOrder.LITTLE_ENDIAN).int
                if (bodyLen < 1) continue

                // Read body
                val body = ByteArray(bodyLen)
                readExact(inputStream!!, body, bodyLen)

                // Track received bytes for bitrate calculation
                val now = System.nanoTime()
                bitrateBytes += bodyLen
                if (lastBitrateTime == 0L) lastBitrateTime = now
                val elapsed = (now - lastBitrateTime) / 1_000_000_000f
                if (elapsed >= 1.0f) {
                    val kbps = (bitrateBytes * 8f / 1000f / elapsed).toInt()
                    bitrateBytes = 0
                    lastBitrateTime = now
                    callback.onBitrateChanged(kbps)
                }

                val flags = body[0].toInt() and 0xFF
                val payload = body.copyOfRange(1, bodyLen)

                if (flags and FLAG_CONTROL != 0) {
                    handleControl(payload)
                } else {
                    val isKey = (flags and FLAG_KEYFRAME) != 0
                    val isConfig = (flags and FLAG_CONFIG) != 0
                    callback.onVideoFrame(payload, isKey, isConfig)
                }
            } catch (e: Exception) {
                if (running) {
                    Log.e(TAG, "Read error: ${e.message}")
                    callback.onDisconnected("连接断开")
                }
                break
            }
        }
    }

    private fun handleControl(payload: ByteArray) {
        try {
            val json = JSONObject(String(payload, Charsets.UTF_8))
            val type = json.optString("type")
            when (type) {
                "pong" -> {
                    val id = json.optInt("id")
                    val now = System.currentTimeMillis()
                    val latency = now - lastPingTime
                    callback.onLatencyMeasured(latency)
                }
                "welcome" -> {
                    // Already handled in handshake
                }
                else -> callback.onControlMessage(json)
            }
        } catch (e: Exception) {
            Log.w(TAG, "Invalid control message: ${e.message}")
        }
    }

    private fun sendControlFrame(json: JSONObject) {
        try {
            val payload = json.toString().toByteArray(Charsets.UTF_8)
            val bodyLen = 1 + payload.size
            val buf = ByteBuffer.allocate(4 + bodyLen).order(ByteOrder.LITTLE_ENDIAN)
            buf.putInt(bodyLen)
            buf.put(0x80.toByte())
            buf.put(payload)
            outputStream!!.write(buf.array())
            outputStream!!.flush()
        } catch (e: Exception) {
            Log.w(TAG, "Send control error: ${e.message}")
        }
    }

    private fun readFrame(): Pair<Int, ByteArray> {
        return try {
            val headerBuf = ByteArray(4)
            readExact(inputStream!!, headerBuf, 4)
            val bodyLen = ByteBuffer.wrap(headerBuf).order(ByteOrder.LITTLE_ENDIAN).int
            if (bodyLen < 1) return Pair(-1, ByteArray(0))
            val body = ByteArray(bodyLen)
            readExact(inputStream!!, body, bodyLen)
            Pair(body[0].toInt() and 0xFF, body.copyOfRange(1, bodyLen))
        } catch (e: Exception) {
            Pair(-1, ByteArray(0))
        }
    }

    private fun readExact(input: InputStream, buf: ByteArray, len: Int) {
        var offset = 0
        while (offset < len) {
            val read = input.read(buf, offset, len - offset)
            if (read < 0) throw java.io.EOFException("Socket closed")
            offset += read
        }
    }

    /**
     * Send a ping to measure latency. Called periodically from the UI thread.
     */
    fun sendPing() {
        if (!running) return
        pingId++
        lastPingTime = System.currentTimeMillis()
        inputSender?.sendPing(pingId)
    }
}