package com.psp.app

import android.util.Log
import org.json.JSONObject
import java.net.*
import java.util.concurrent.CopyOnWriteArrayList

/**
 * UDP 局域网发现客户端。
 *
 * 在 Android 端:
 * 1. 发送 UDP 广播发现请求 (psp_discover)
 * 2. 监听 PC 端的响应 (psp_announce)
 * 3. 维护发现的 PC 列表，自动更新
 */
class DiscoveryClient(
    private val callback: DiscoveryCallback
) {
    companion object {
        private const val TAG = "DiscoveryClient"
        private const val DISCOVERY_PORT = 4748
        private const val DISCOVER_INTERVAL_MS = 3000L
        private const val DEVICE_TIMEOUT_MS = 10000L  // 10s no response = gone
    }

    interface DiscoveryCallback {
        /** 发现新设备或设备列表更新 */
        fun onDevicesUpdated(devices: List<DiscoveredHost>)
        /** 发现错误 */
        fun onError(message: String)
    }

    data class DiscoveredHost(
        val hostname: String,
        val displayName: String,
        val ips: List<String>,
        val port: Int,
        val version: String,
        val lastSeen: Long = System.currentTimeMillis()
    ) {
        /** 优先显示 displayName，否则 hostname */
        val label: String get() = displayName.ifBlank { hostname }
        /** 第一个可用的 IP */
        val primaryIp: String get() = ips.firstOrNull() ?: ""
    }

    private val discoveredDevices = CopyOnWriteArrayList<DiscoveredHost>()
    private var running = false
    private var discoverThread: Thread? = null
    private var cleanupThread: Thread? = null
    private var udpSocket: DatagramSocket? = null

    fun start() {
        if (running) return
        running = true

        discoverThread = Thread({ runDiscoveryLoop() }, "DiscoveryClient")
        discoverThread?.start()

        cleanupThread = Thread({ runCleanupLoop() }, "DiscoveryCleanup")
        cleanupThread?.start()

        Log.i(TAG, "Discovery started")
    }

    fun stop() {
        running = false
        if (udpSocket != null) {
            try {
                udpSocket?.close()
            } catch (_: Exception) {}
            udpSocket = null
        }
        try {
            discoverThread?.join(2000)
        } catch (_: Exception) {}
        try {
            cleanupThread?.join(2000)
        } catch (_: Exception) {}
        discoveredDevices.clear()
    }

    /** 获取当前发现的设备列表 */
    fun getDevices(): List<DiscoveredHost> = discoveredDevices.toList()

    /** 手动触发一次扫描 */
    fun requestDiscovery() {
        sendDiscoveryProbe()
    }

    private fun runDiscoveryLoop() {
        try {
            // Create UDP socket for listening
            udpSocket = DatagramSocket(DISCOVERY_PORT)
            udpSocket?.broadcast = true
            udpSocket?.soTimeout = 1000

            // Send initial discovery probe
            sendDiscoveryProbe()

            val buffer = ByteArray(4096)
            while (running) {
                try {
                    val packet = DatagramPacket(buffer, buffer.size)
                    udpSocket?.receive(packet)

                    val data = String(packet.data, 0, packet.length, Charsets.UTF_8)
                    handlePacket(data, packet.address)
                } catch (e: SocketTimeoutException) {
                    // Timeout is normal, just loop
                    continue
                } catch (e: Exception) {
                    if (running) {
                        Log.w(TAG, "Receive error: ${e.message}")
                    }
                    break
                }
            }
        } catch (e: Exception) {
            if (running) {
                Log.e(TAG, "Discovery loop error: ${e.message}")
                callback.onError("发现服务启动失败: ${e.message}")
            }
        }
    }

    private fun runCleanupLoop() {
        while (running) {
            try {
                Thread.sleep(5000)
                val now = System.currentTimeMillis()
                var changed = false
                val iterator = discoveredDevices.iterator()
                while (iterator.hasNext()) {
                    val device = iterator.next()
                    if (now - device.lastSeen > DEVICE_TIMEOUT_MS) {
                        iterator.remove()
                        changed = true
                        Log.d(TAG, "Device timed out: ${device.label}")
                    }
                }
                if (changed) {
                    callback.onDevicesUpdated(discoveredDevices.toList())
                }
            } catch (_: InterruptedException) {
                break
            }
        }
    }

    private fun sendDiscoveryProbe() {
        try {
            val msg = JSONObject().apply {
                put("type", "psp_discover")
                put("device", android.os.Build.MODEL)
            }
            val data = msg.toString().toByteArray(Charsets.UTF_8)

            // Broadcast to LAN
            val broadcastAddr = InetAddress.getByName("255.255.255.255")
            val packet = DatagramPacket(data, data.size, broadcastAddr, DISCOVERY_PORT)
            udpSocket?.send(packet)

            Log.d(TAG, "Discovery probe sent")
        } catch (e: Exception) {
            Log.w(TAG, "Failed to send discovery probe: ${e.message}")
        }
    }

    private fun handlePacket(data: String, sourceAddr: InetAddress) {
        try {
            val json = JSONObject(data)
            val type = json.optString("type", "")

            if (type == "psp_announce") {
                val hostname = json.optString("hostname", "unknown")
                val displayName = json.optString("display_name", hostname)
                val port = json.optInt("port", 4747)
                val version = json.optString("version", "0.0.0")

                // Get IPs from the message, or use source address
                val ips = mutableListOf<String>()
                val jsonIps = json.optJSONArray("ips")
                if (jsonIps != null) {
                    for (i in 0 until jsonIps.length()) {
                        ips.add(jsonIps.getString(i))
                    }
                }
                // Always include the source IP
                val sourceIp = sourceAddr.hostAddress ?: ""
                if (sourceIp !in ips) {
                    ips.add(0, sourceIp)
                }

                val device = DiscoveredHost(
                    hostname = hostname,
                    displayName = displayName,
                    ips = ips,
                    port = port,
                    version = version,
                    lastSeen = System.currentTimeMillis()
                )

                // Update or add
                var found = false
                for (i in discoveredDevices.indices) {
                    val existing = discoveredDevices[i]
                    if (existing.hostname == hostname ||
                        existing.ips.intersect(device.ips).isNotEmpty()) {
                        discoveredDevices[i] = device
                        found = true
                        break
                    }
                }
                if (!found) {
                    discoveredDevices.add(device)
                    Log.i(TAG, "Discovered: ${device.label} @ ${device.primaryIp}:${device.port}")
                }

                callback.onDevicesUpdated(discoveredDevices.toList())
            }
        } catch (e: Exception) {
            Log.w(TAG, "Invalid discovery packet: ${e.message}")
        }
    }
}