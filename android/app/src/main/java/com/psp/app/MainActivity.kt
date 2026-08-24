package com.psp.app

import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.*
import android.widget.*
import androidx.appcompat.app.AppCompatActivity
import org.json.JSONObject

/**
 * PSP Android 主界面。
 *
 * 三种连接模式切换:
 * - WiFi: UDP 自动发现 PC + 手动输入 IP
 * - USB: 通过 ADB reverse 隧道连接 127.0.0.1
 * - 无线 ADB: 通过无线 ADB reverse 隧道连接 127.0.0.1
 */
class MainActivity : AppCompatActivity() {

    companion object {
        private const val TAG = "MainActivity"
    }

    // Views
    private lateinit var surfaceView: SurfaceView
    private lateinit var connectOverlay: ScrollView
    private lateinit var statsOverlay: LinearLayout
    private lateinit var disconnectBtn: ImageButton

    // Tabs
    private lateinit var tabWifi: TextView
    private lateinit var tabUsb: TextView
    private lateinit var tabWireless: TextView
    private lateinit var panelWifi: LinearLayout
    private lateinit var panelUsb: LinearLayout
    private lateinit var panelWireless: LinearLayout

    // WiFi panel
    private lateinit var deviceList: ListView
    private lateinit var noDevices: TextView
    private lateinit var ipInput: EditText
    private lateinit var portInput: EditText
    private lateinit var connectBtn: Button

    // USB panel
    private lateinit var connectUsbBtn: Button

    // Wireless panel
    private lateinit var connectWirelessBtn: Button

    // Settings
    private lateinit var settingsBtn: Button

    // Stats
    private lateinit var statsFps: TextView
    private lateinit var statsLatency: TextView
    private lateinit var statsBitrate: TextView
    private lateinit var statsResolution: TextView

    // State
    private var streamClient: StreamClient? = null
    private var decoderThread: DecoderThread? = null
    private var currentSettings = StreamSettings()
    private var discoveryClient: DiscoveryClient? = null
    private val mainHandler = Handler(Looper.getMainLooper())
    private var statsUpdateRunnable: Runnable? = null
    private var pingRunnable: Runnable? = null
    private var isConnected = false
    private var touchDown = false

    // Device list adapter
    private val discoveredHosts = mutableListOf<DiscoveryClient.DiscoveredHost>()
    private lateinit var deviceAdapter: ArrayAdapter<String>

    // Current mode: "wifi", "usb", "wireless"
    private var currentMode = "wifi"

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        // 全屏沉浸式
        window.addFlags(WindowManager.LayoutParams.FLAG_FULLSCREEN)
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        window.decorView.systemUiVisibility = (
            View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY or
            View.SYSTEM_UI_FLAG_FULLSCREEN or
            View.SYSTEM_UI_FLAG_HIDE_NAVIGATION
        )

        setContentView(R.layout.activity_main)

        initViews()
        setupTabs()
        setupButtons()
        setupTouchInput()
        startDiscovery()
    }

    // ==================== 初始化 ====================

    private fun initViews() {
        surfaceView = findViewById(R.id.surfaceView)
        connectOverlay = findViewById(R.id.connectOverlay)
        statsOverlay = findViewById(R.id.statsOverlay)
        disconnectBtn = findViewById(R.id.disconnectBtn)

        tabWifi = findViewById(R.id.tabWifi)
        tabUsb = findViewById(R.id.tabUsb)
        tabWireless = findViewById(R.id.tabWireless)
        panelWifi = findViewById(R.id.panelWifi)
        panelUsb = findViewById(R.id.panelUsb)
        panelWireless = findViewById(R.id.panelWireless)

        deviceList = findViewById(R.id.deviceList)
        noDevices = findViewById(R.id.noDevices)
        ipInput = findViewById(R.id.ipInput)
        portInput = findViewById(R.id.portInput)
        connectBtn = findViewById(R.id.connectBtn)

        connectUsbBtn = findViewById(R.id.connectUsbBtn)
        connectWirelessBtn = findViewById(R.id.connectWirelessBtn)
        settingsBtn = findViewById(R.id.settingsBtn)

        statsFps = findViewById(R.id.statsFps)
        statsLatency = findViewById(R.id.statsLatency)
        statsBitrate = findViewById(R.id.statsBitrate)
        statsResolution = findViewById(R.id.statsResolution)

        // 设备列表适配器
        deviceAdapter = ArrayAdapter(this, android.R.layout.simple_list_item_1, mutableListOf())
        deviceList.adapter = deviceAdapter
        deviceList.setOnItemClickListener { _, _, position, _ ->
            if (position < discoveredHosts.size) {
                val host = discoveredHosts[position]
                ipInput.setText(host.primaryIp)
                portInput.setText(host.port.toString())
            }
        }
    }

    // ==================== 模式切换 ====================

    private fun setupTabs() {
        val tabClick = View.OnClickListener { view ->
            when (view.id) {
                R.id.tabWifi -> switchMode("wifi")
                R.id.tabUsb -> switchMode("usb")
                R.id.tabWireless -> switchMode("wireless")
            }
        }
        tabWifi.setOnClickListener(tabClick)
        tabUsb.setOnClickListener(tabClick)
        tabWireless.setOnClickListener(tabClick)
    }

    private fun switchMode(mode: String) {
        currentMode = mode

        // 重置所有标签样式
        val inactiveColor = "#AAAAAA"
        val activeColor = "#FFFFFF"
        val activeBg = "#FF6200EE"
        val inactiveBg = "#333333"

        listOf(tabWifi, tabUsb, tabWireless).forEach {
            it.setTextColor(android.graphics.Color.parseColor(inactiveColor))
            it.setBackgroundColor(android.graphics.Color.parseColor(inactiveBg))
        }

        // 隐藏所有面板
        panelWifi.visibility = View.GONE
        panelUsb.visibility = View.GONE
        panelWireless.visibility = View.GONE

        // 激活选中标签和面板
        when (mode) {
            "wifi" -> {
                tabWifi.setTextColor(android.graphics.Color.parseColor(activeColor))
                tabWifi.setBackgroundColor(android.graphics.Color.parseColor(activeBg))
                panelWifi.visibility = View.VISIBLE
                // 手动触发一次扫描
                discoveryClient?.requestDiscovery()
            }
            "usb" -> {
                tabUsb.setTextColor(android.graphics.Color.parseColor(activeColor))
                tabUsb.setBackgroundColor(android.graphics.Color.parseColor(activeBg))
                panelUsb.visibility = View.VISIBLE
            }
            "wireless" -> {
                tabWireless.setTextColor(android.graphics.Color.parseColor(activeColor))
                tabWireless.setBackgroundColor(android.graphics.Color.parseColor(activeBg))
                panelWireless.visibility = View.VISIBLE
            }
        }
    }

    // ==================== 按钮事件 ====================

    private fun setupButtons() {
        // WiFi 连接
        connectBtn.setOnClickListener {
            val host = ipInput.text.toString().trim()
            val port = portInput.text.toString().trim().toIntOrNull() ?: 4747
            if (host.isEmpty()) {
                ipInput.error = "请输入主机 IP"
                return@setOnClickListener
            }
            connect(host, port)
        }

        // USB 连接
        connectUsbBtn.setOnClickListener {
            val port = portInput.text.toString().trim().toIntOrNull() ?: 4747
            connect("127.0.0.1", port)
        }

        // 无线 ADB 连接
        connectWirelessBtn.setOnClickListener {
            val port = portInput.text.toString().trim().toIntOrNull() ?: 4747
            connect("127.0.0.1", port)
        }

        // 设置
        settingsBtn.setOnClickListener {
            if (!isConnected) {
                val dialog = SettingsDialog(this, currentSettings) { newSettings ->
                    currentSettings = newSettings
                }
                dialog.show()
            }
        }

        // 断开
        disconnectBtn.setOnClickListener {
            disconnect()
        }
    }

    // ==================== 局域网发现 ====================

    private fun startDiscovery() {
        discoveryClient = DiscoveryClient(object : DiscoveryClient.DiscoveryCallback {
            override fun onDevicesUpdated(devices: List<DiscoveryClient.DiscoveredHost>) {
                mainHandler.post {
                    updateDeviceList(devices)
                }
            }

            override fun onError(message: String) {
                mainHandler.post {
                    noDevices.text = "发现错误: $message"
                }
            }
        })
        discoveryClient?.start()
    }

    private fun updateDeviceList(devices: List<DiscoveryClient.DiscoveredHost>) {
        discoveredHosts.clear()
        discoveredHosts.addAll(devices)

        deviceAdapter.clear()
        if (devices.isEmpty()) {
            noDevices.text = "未发现主机，请确保 PC 端已启动 PSP Host"
            noDevices.visibility = View.VISIBLE
        } else {
            noDevices.visibility = View.GONE
            for (d in devices) {
                val ips = d.ips.joinToString(", ")
                deviceAdapter.add("${d.label}\n  $ips:${d.port}")
            }
        }
        deviceAdapter.notifyDataSetChanged()
    }

    // ==================== 触摸输入 ====================

    private fun setupTouchInput() {
        surfaceView.setOnTouchListener { _, event ->
            if (!isConnected) return@setOnTouchListener true

            val streamClient = streamClient ?: return@setOnTouchListener true
            val inputSender = streamClient.inputSender ?: return@setOnTouchListener true

            val width = surfaceView.width.toFloat()
            val height = surfaceView.height.toFloat()
            if (width <= 0 || height <= 0) return@setOnTouchListener true

            val nx = (event.x / width).coerceIn(0f, 1f)
            val ny = (event.y / height).coerceIn(0f, 1f)

            when (event.actionMasked) {
                MotionEvent.ACTION_DOWN -> {
                    touchDown = true
                    inputSender.sendButton(nx, ny, 1, 1)
                }
                MotionEvent.ACTION_MOVE -> {
                    if (touchDown) {
                        inputSender.sendMove(nx, ny)
                    }
                }
                MotionEvent.ACTION_UP -> {
                    touchDown = false
                    inputSender.sendButton(nx, ny, 1, 0)
                }
                MotionEvent.ACTION_SCROLL -> {
                    inputSender.sendWheel(
                        event.getAxisValue(MotionEvent.AXIS_HSCROLL),
                        event.getAxisValue(MotionEvent.AXIS_VSCROLL)
                    )
                }
            }
            return@setOnTouchListener true
        }
    }

    // ==================== 连接 / 断开 ====================

    private fun connect(host: String, port: Int) {
        if (isConnected) return

        val bitrateKbps = calculateBitrate(
            currentSettings.width, currentSettings.height,
            currentSettings.fps, currentSettings.qualityMultiplier
        )

        val settings = StreamClient.ConnectionSettings(
            width = currentSettings.width,
            height = currentSettings.height,
            fps = currentSettings.fps,
            bitrateKbps = bitrateKbps,
            codec = "h264"
        )

        connectOverlay.visibility = View.GONE
        statsOverlay.visibility = View.VISIBLE
        disconnectBtn.visibility = View.VISIBLE

        streamClient = StreamClient(host, port, settings, object : StreamClient.ClientCallback {
            override fun onConnected(params: StreamClient.SessionParams) {
                mainHandler.post {
                    isConnected = true
                    startStatsUpdates()
                    startDecoder(params)
                }
            }

            override fun onVideoFrame(data: ByteArray, isKeyframe: Boolean, isConfig: Boolean) {
                decoderThread?.queueFrame(data, isKeyframe, isConfig)
            }

            override fun onControlMessage(json: JSONObject) {}

            override fun onDisconnected(reason: String) {
                mainHandler.post {
                    disconnect()
                    Toast.makeText(this@MainActivity, "已断开: $reason", Toast.LENGTH_SHORT).show()
                }
            }

            override fun onLatencyMeasured(ms: Long) {
                mainHandler.post {
                    statsLatency.text = "延迟: ${ms}ms"
                }
            }
        })

        streamClient?.start()
    }

    private fun startDecoder(params: StreamClient.SessionParams) {
        statsResolution.text = "${params.width}x${params.height} @ ${params.fps}fps"

        decoderThread?.stop()
        decoderThread = DecoderThread(
            codec = params.codec,
            surface = surfaceView.holder.surface,
            callback = object : DecoderThread.DecoderCallback {
                override fun onFrameDecoded() {}
                override fun onError(message: String) {
                    mainHandler.post {
                        Toast.makeText(this@MainActivity, message, Toast.LENGTH_LONG).show()
                    }
                }
            }
        )
        decoderThread?.start()
    }

    private fun startStatsUpdates() {
        statsUpdateRunnable = object : Runnable {
            override fun run() {
                if (isConnected) {
                    val fps = decoderThread?.getCurrentFps() ?: 0f
                    statsFps.text = "FPS: ${"%.1f".format(fps)}"
                    mainHandler.postDelayed(this, 1000)
                }
            }
        }
        mainHandler.post(statsUpdateRunnable!!)

        pingRunnable = object : Runnable {
            override fun run() {
                if (isConnected) {
                    streamClient?.sendPing()
                    mainHandler.postDelayed(this, 3000)
                }
            }
        }
        mainHandler.post(pingRunnable!!)
    }

    private fun disconnect() {
        isConnected = false
        decoderThread?.stop()
        decoderThread = null
        streamClient?.stop()
        streamClient = null

        statsUpdateRunnable?.let { mainHandler.removeCallbacks(it) }
        pingRunnable?.let { mainHandler.removeCallbacks(it) }

        connectOverlay.visibility = View.VISIBLE
        statsOverlay.visibility = View.GONE
        disconnectBtn.visibility = View.GONE
    }

    override fun onDestroy() {
        disconnect()
        discoveryClient?.stop()
        super.onDestroy()
    }

    override fun onWindowFocusChanged(hasFocus: Boolean) {
        super.onWindowFocusChanged(hasFocus)
        if (hasFocus) {
            window.decorView.systemUiVisibility = (
                View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY or
                View.SYSTEM_UI_FLAG_FULLSCREEN or
                View.SYSTEM_UI_FLAG_HIDE_NAVIGATION
            )
        }
    }

    private fun calculateBitrate(width: Int, height: Int, fps: Int, quality: Float): Int {
        val baseKbps = 10000
        val pixelRatio = (width * height).toFloat() / (1920 * 1080)
        val fpsRatio = fps.toFloat() / 60f
        return (baseKbps * pixelRatio * fpsRatio * quality).toInt()
    }
}