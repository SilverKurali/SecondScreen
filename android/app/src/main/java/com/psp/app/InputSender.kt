package com.psp.app

import android.util.Log
import org.json.JSONObject
import java.io.OutputStream
import java.nio.ByteBuffer
import java.nio.ByteOrder

/**
 * Sends touch/mouse input events from the Android device back to the PC host.
 *
 * All input coordinates are normalized to 0..1 relative to the streamed video frame,
 * so the host can map them to the correct screen region regardless of
 * the Android device's display resolution.
 */
class InputSender(private val outputStream: OutputStream) {

    private val TAG = "InputSender"
    private val sendQueue = java.util.concurrent.ConcurrentLinkedQueue<ByteArray>()
    private var sending = false
    private var senderThread: Thread? = null

    init {
        sending = true
        senderThread = Thread({ runSenderLoop() }, "InputSender")
        senderThread?.isDaemon = true
        senderThread?.start()
    }

    private fun runSenderLoop() {
        while (sending) {
            try {
                val data = sendQueue.poll()
                if (data != null) {
                    synchronized(outputStream) {
                        outputStream.write(data, 0, data.size)
                        outputStream.flush()
                    }
                } else {
                    Thread.sleep(1)
                }
            } catch (e: Exception) {
                if (sending) {
                    Log.w(TAG, "Send error: ${e.javaClass.simpleName}: ${e.message}")
                }
            }
        }
    }

    /**
     * Send a mouse move event.
     * @param x Normalized X (0..1)
     * @param y Normalized Y (0..1)
     */
    fun sendMove(x: Float, y: Float) {
        val msg = JSONObject().apply {
            put("type", "input")
            put("kind", "move")
            put("x", x.toDouble())
            put("y", y.toDouble())
        }
        sendControl(msg)
    }

    /**
     * Send a mouse button event.
     * @param x Normalized X
     * @param y Normalized Y
     * @param btn Button ID: 1=left, 2=right, 3=middle
     * @param state 1=pressed, 0=released
     */
    fun sendButton(x: Float, y: Float, btn: Int, state: Int) {
        val msg = JSONObject().apply {
            put("type", "input")
            put("kind", "btn")
            put("x", x.toDouble())
            put("y", y.toDouble())
            put("btn", btn)
            put("state", state)
        }
        sendControl(msg)
    }

    /**
     * Send a scroll/wheel event.
     */
    fun sendWheel(dx: Float, dy: Float) {
        val msg = JSONObject().apply {
            put("type", "input")
            put("kind", "wheel")
            put("dx", dx.toDouble())
            put("dy", dy.toDouble())
        }
        sendControl(msg)
    }

    /**
     * Send a ping message to measure round-trip latency.
     */
    fun sendPing(id: Int) {
        val msg = JSONObject().apply {
            put("type", "ping")
            put("id", id)
        }
        sendControl(msg)
    }

    private fun sendControl(msg: JSONObject) {
        try {
            val payload = msg.toString().toByteArray(Charsets.UTF_8)
            val bodyLen = 1 + payload.size
            val buf = ByteArray(4 + bodyLen)
            val bb = ByteBuffer.wrap(buf)
            bb.order(ByteOrder.LITTLE_ENDIAN)
            bb.putInt(bodyLen)
            bb.put(0x80.toByte())  // FLAG_CONTROL
            bb.put(payload)
            sendQueue.add(buf)
        } catch (e: Exception) {
            Log.w(TAG, "Failed to queue input: ${e.javaClass.simpleName}: ${e.message}")
        }
    }

}