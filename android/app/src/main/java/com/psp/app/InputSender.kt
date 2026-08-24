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
    private var sendBuffer = ByteArray(4096)

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
            val payload = msg.toString().encodeToByteArray(Charsets.UTF_8)
            val bodyLen = 1 + payload.size  // 1 byte flags + payload
            if (4 + bodyLen > sendBuffer.size) {
                sendBuffer = ByteArray(4 + bodyLen + 1024)
            }
            var buf = ByteBuffer.wrap(sendBuffer)
            buf.order(ByteOrder.LITTLE_ENDIAN)
            buf.putInt(bodyLen)
            buf.put(0x80.toByte())  // FLAG_CONTROL
            buf.put(payload)
            outputStream.write(sendBuffer, 0, 4 + bodyLen)
            outputStream.flush()
        } catch (e: Exception) {
            Log.w(TAG, "Failed to send input: ${e.message}")
        }
    }
}