package com.psp.app

import android.app.Activity
import android.app.AlertDialog
import android.view.LayoutInflater
import android.widget.RadioGroup
import android.widget.SeekBar
import android.widget.TextView

/**
 * Settings dialog for adjusting stream quality parameters.
 *
 * Allows the user to select:
 * - Resolution: 720p (1280x720), 1080p (1920x1080), 2K (2560x1440)
 * - Frame rate: 60, 90, 120 fps
 * - Quality: bitrate multiplier 0.5x - 3.0x
 */
data class StreamSettings(
    val width: Int = 1920,
    val height: Int = 1080,
    val fps: Int = 60,
    val qualityMultiplier: Float = 1.0f,
    val displayMode: Int = 0,  // 0=镜像(mirror), 1=扩展(extend)
    val useHardwareEncoder: Boolean = false  // false=软件x264, true=硬件nvh264enc
)

class SettingsDialog(
    private val activity: Activity,
    private val currentSettings: StreamSettings,
    private val onSave: (StreamSettings) -> Unit
) {
    private val qualityLabels = arrayOf(
        "最低 (0.5x)", "低 (0.7x)", "中低 (0.8x)", "中 (0.9x)",
        "中等 (1.0x)", "中高 (1.2x)", "高 (1.5x)", "很高 (1.8x)",
        "极高 (2.0x)", "超高 (2.5x)", "极限 (3.0x)"
    )

    private val qualityValues = arrayOf(
        0.5f, 0.7f, 0.8f, 0.9f,
        1.0f, 1.2f, 1.5f, 1.8f,
        2.0f, 2.5f, 3.0f
    )

    fun show() {
        val builder = AlertDialog.Builder(activity)
        builder.setTitle("画质设置")

        val inflater = LayoutInflater.from(activity)
        val view = inflater.inflate(R.layout.dialog_settings, null)
        builder.setView(view)

        // Resolution
        val resGroup = view.findViewById<RadioGroup>(R.id.resolutionGroup)
        when {
            currentSettings.width >= 2560 -> resGroup.check(R.id.res2K)
            currentSettings.width >= 1920 -> resGroup.check(R.id.res1080p)
            else -> resGroup.check(R.id.res720p)
        }

        // FPS
        val fpsGroup = view.findViewById<RadioGroup>(R.id.fpsGroup)
        when (currentSettings.fps) {
            120 -> fpsGroup.check(R.id.fps120)
            90 -> fpsGroup.check(R.id.fps90)
            else -> fpsGroup.check(R.id.fps60)
        }

        // Quality
        val qualitySeekBar = view.findViewById<SeekBar>(R.id.qualitySeekBar)
        val qualityLabel = view.findViewById<TextView>(R.id.qualityLabel)

        // Find closest quality index
        var qualityIndex = 4  // Default to 1.0x
        for (i in qualityValues.indices) {
            if (currentSettings.qualityMultiplier <= qualityValues[i]) {
                qualityIndex = i
                break
            }
        }
        qualitySeekBar.progress = qualityIndex
        qualityLabel.text = qualityLabels[qualityIndex]

        qualitySeekBar.setOnSeekBarChangeListener(object : SeekBar.OnSeekBarChangeListener {
            override fun onProgressChanged(seekBar: SeekBar?, progress: Int, fromUser: Boolean) {
                qualityLabel.text = qualityLabels[progress.coerceIn(0, qualityLabels.size - 1)]
            }
            override fun onStartTrackingTouch(seekBar: SeekBar?) {}
            override fun onStopTrackingTouch(seekBar: SeekBar?) {}
        })

        builder.setPositiveButton("确定") { _, _ ->
            // Resolve resolution
            val (w, h) = when (resGroup.checkedRadioButtonId) {
                R.id.res720p -> 1280 to 720
                R.id.res2K -> 2560 to 1440
                else -> 1920 to 1080
            }

            // Resolve FPS
            val fps = when (fpsGroup.checkedRadioButtonId) {
                R.id.fps120 -> 120
                R.id.fps90 -> 90
                else -> 60
            }

            // Resolve quality
            val qIdx = qualitySeekBar.progress.coerceIn(0, qualityValues.size - 1)
            val quality = qualityValues[qIdx]

            onSave(StreamSettings(w, h, fps, quality,
                currentSettings.displayMode, currentSettings.useHardwareEncoder))
        }

        builder.setNegativeButton("取消", null)
        builder.show()
    }
}