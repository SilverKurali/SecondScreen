package com.psp.app

import android.media.MediaCodec
import android.media.MediaFormat
import android.os.Build
import android.util.Log
import android.view.Surface
import java.util.concurrent.ConcurrentLinkedQueue

/**
 * Decodes H.264/VP9/VP8 video frames from the PSP host using Android MediaCodec.
 *
 * Features:
 * - Low-latency decoding (KEY_LOW_LATENCY on API 30+)
 * - Frame dropping when queue is too deep
 * - Surface-based rendering for zero-copy display
 * - FPS counter for stats
 */
class DecoderThread(
    private val codec: String,
    private val width: Int,
    private val height: Int,
    private val surface: Surface,
    private val callback: DecoderCallback
) {
    companion object {
        private const val TAG = "DecoderThread"
        private const val MAX_QUEUE_SIZE = 3
        private const val TIMEOUT_US = 10000L  // 10ms
    }

    interface DecoderCallback {
        fun onFrameDecoded()
        fun onError(message: String)
    }

    private var mediaCodec: MediaCodec? = null
    private val frameQueue = ConcurrentLinkedQueue<FrameData>()
    private var running = false
    private var decoderThread: Thread? = null
    private var decodedFrames = 0L
    private var lastStatsTime = 0L
    private var currentFps = 0f

    data class FrameData(
        val data: ByteArray,
        val isKeyframe: Boolean,
        val isConfig: Boolean
    )

    fun start() {
        running = true
        decoderThread = Thread({ runLoop() }, "DecoderThread")
        decoderThread?.start()
    }

    fun stop() {
        running = false
        try {
            decoderThread?.join(1000)
        } catch (_: Exception) {}
        mediaCodec?.stop()
        mediaCodec?.release()
        mediaCodec = null
        frameQueue.clear()
    }

    /**
     * Queue a frame for decoding. Called from the StreamClient thread.
     */
    fun queueFrame(data: ByteArray, isKeyframe: Boolean, isConfig: Boolean) {
        if (!running) return

        // Drop non-keyframes when queue is full
        if (frameQueue.size >= MAX_QUEUE_SIZE) {
            if (!isKeyframe) return
            // Clear queue for keyframe
            frameQueue.clear()
        }

        frameQueue.add(FrameData(data, isKeyframe, isConfig))
    }

    fun getCurrentFps(): Float = currentFps

    private fun runLoop() {
        try {
            if (!initCodec()) return

            while (running) {
                val frame = frameQueue.peek()
                if (frame == null) {
                    Thread.sleep(1)
                    continue
                }

                if (decodeFrame(frame)) {
                    frameQueue.poll()  // 解码成功后才移除
                } else {
                    // 解码器忙,稍后重试
                    Thread.sleep(1)
                }
            }
        } catch (e: Exception) {
            Log.e(TAG, "Decoder error: ${e.message}")
            callback.onError("解码错误: ${e.message}")
        }
    }

    private fun initCodec(): Boolean {
        // Determine MIME type
        val mime = when (codec) {
            "h264" -> MediaFormat.MIMETYPE_VIDEO_AVC
            "vp9" -> MediaFormat.MIMETYPE_VIDEO_VP9
            "vp8" -> MediaFormat.MIMETYPE_VIDEO_VP8
            "theora" -> {
                callback.onError("Theora not supported on Android")
                return false
            }
            else -> {
                callback.onError("Unsupported codec: $codec")
                return false
            }
        }

        Log.i(TAG, "Initializing decoder for $codec (MIME: $mime)")

        try {
            val format = MediaFormat.createVideoFormat(mime, width, height)
            format.setInteger(MediaFormat.KEY_MAX_INPUT_SIZE, 2 * 1024 * 1024)  // 2MB

            // Low latency mode (Android 11+)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
                format.setInteger(MediaFormat.KEY_LOW_LATENCY, 1)
                format.setInteger("vendor.qti-ext-dec-low-latency.enable", 1)
            }

            // Priority: low latency over quality (Android 10+)
            if (Build.VERSION.SDK_INT >= 29) {
                format.setInteger(MediaFormat.KEY_PRIORITY, 0)  // 0 = realtime
            }

            val codec = MediaCodec.createDecoderByType(mime)
            codec.configure(format, surface, null, 0)
            codec.start()
            mediaCodec = codec
            lastStatsTime = System.nanoTime()
            Log.i(TAG, "Decoder initialized successfully")
            return true

        } catch (e: Exception) {
            Log.e(TAG, "Failed to init decoder: ${e.message}")
            callback.onError("初始化解码器失败: ${e.message}")
            return false
        }
    }

    private fun decodeFrame(frame: FrameData): Boolean {
        val codec = mediaCodec ?: return false

        try {
            // Get input buffer (try with longer timeout)
            var index = codec.dequeueInputBuffer(50000L)  // 50ms
            if (index < 0) {
                // If no buffer available, try draining output first
                drainOutput(codec)
                // Try again with shorter timeout
                index = codec.dequeueInputBuffer(10000L)
                if (index < 0) return false  // Still busy, retry later
            }

            val inputBuffer = codec.getInputBuffer(index) ?: return false
            inputBuffer.clear()
            inputBuffer.put(frame.data)
            val flags = if (frame.isKeyframe) MediaCodec.BUFFER_FLAG_KEY_FRAME else 0
            codec.queueInputBuffer(index, 0, frame.data.size, 0, flags)

            // Drain output to free buffers
            drainOutput(codec)

            return true
        } catch (e: Exception) {
            Log.w(TAG, "Decode error: ${e.message}")
            return false
        }
    }

    private fun drainOutput(codec: MediaCodec) {
        val bufferInfo = MediaCodec.BufferInfo()
        while (true) {
            val outIndex = codec.dequeueOutputBuffer(bufferInfo, 0L)  // 不阻塞
            when {
                outIndex >= 0 -> {
                    codec.releaseOutputBuffer(outIndex, true)
                    decodedFrames++

                    // Calculate FPS
                    val now = System.nanoTime()
                    val elapsed = (now - lastStatsTime) / 1_000_000_000f
                    if (elapsed >= 1.0f) {
                        currentFps = decodedFrames.toFloat() / elapsed
                        decodedFrames = 0
                        lastStatsTime = now
                    }
                }
                outIndex == MediaCodec.INFO_OUTPUT_FORMAT_CHANGED -> {
                    Log.d(TAG, "Output format changed: ${codec.outputFormat}")
                }
                outIndex == MediaCodec.INFO_TRY_AGAIN_LATER -> {
                    break  // 没有更多输出了
                }
                else -> break
            }
        }
    }
}