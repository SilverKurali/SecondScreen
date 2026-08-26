package com.psp.app

import android.content.res.Configuration
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
 * 两种连接模式切换:
 * - WiFi: UDP 自动发现 PC + 手动输入 IP
 * - USB: 通过 ADB reverse 隧道连接 127.0.0.1
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
    private lateinit var panelWifi: LinearLayout
    private lateinit var panelUsb: LinearLayout

    // WiFi panel
    private lateinit var deviceList: ListView
    private lateinit var noDevices: TextView
    private lateinit var ipInput: EditText
    private lateinit var portInput: EditText
    private lateinit var connectBtn: Button
    private lateinit var ipSpinner: Spinner

    // USB panel
    private lateinit var connectUsbBtn: Button

    // Settings
    private lateinit var settingsBtn: Button

    // Stats
    private lateinit var statsFps: TextView
    private lateinit var statsLatency: TextView
    private lateinit var statsBitrate: TextView
    private lateinit var statsResolution: TextView

    // Floating bubble
    private var floatingBubble: View? = null
    private var isMenuVisible = false

    // State
    private var connectedHost: String = ""
    private var connectedPort: Int = 4747
    private var streamClient: StreamClient? = null
    private var decoderThread: DecoderThread? = null
    private var currentSettings = StreamSettings()
    private var discoveryClient: DiscoveryClient? = null
    private val mainHandler = Handler(Looper.getMainLooper())
    private var statsUpdateRunnable: Runnable? = null
    private var pingRunnable: Runnable? = null
    private var isConnected = false
    private var touchDown = false
    private var screenWidthPx = 0
    private var screenHeightPx = 0

    // Device list adapter
    private val discoveredHosts = mutableListOf<DiscoveryClient.DiscoveredHost>()
    private lateinit var deviceAdapter: ArrayAdapter<String>

    // IP spinner adapter
    private lateinit var ipSpinnerAdapter: ArrayAdapter<String>
    private val ipList = mutableListOf<String>()

    // Current mode: "wifi", "usb"
    private var currentMode = "wifi"

    private var longPressRunnable: Runnable? = null
    private var isLongPress = false
    private val LONG_PRESS_MS = 500L

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        window.addFlags(WindowManager.LayoutParams.FLAG_FULLSCREEN)
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        window.decorView.systemUiVisibility = (
            View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY or
            View.SYSTEM_UI_FLAG_FULLSCREEN or
            View.SYSTEM_UI_FLAG_HIDE_NAVIGATION
        )

        setContentView(R.layout.activity_main)

        val display = windowManager.defaultDisplay
        val metrics = android.util.DisplayMetrics()
        if (android.os.Build.VERSION.SDK_INT >= 30) {
            display.getRealMetrics(metrics)
        } else {
            display.getMetrics(metrics)
        }
        screenWidthPx = metrics.widthPixels
        screenHeightPx = metrics.heightPixels
        val ratio = screenWidthPx.toFloat() / screenHeightPx.toFloat()
        if (ratio > 1.0f) {
            currentSettings = StreamSettings(
                width = 1920,
                height = (1920 / ratio).toInt().coerceAtMost(1920),
                fps = 60,
                qualityMultiplier = 1.0f
            )
        } else {
            currentSettings = StreamSettings(
                width = (1080 * ratio).toInt().coerceAtLeast(640),
                height = 1080,
                fps = 60,
                qualityMultiplier = 1.0f
            )
        }

        initViews()
        setupTabs()
        setupButtons()
        setupTouchInput()
        startDiscovery()
        setupFloatingBubble()
    }

    // ==================== 初始化 ====================

    private fun initViews() {
        surfaceView = findViewById(R.id.surfaceView)
        connectOverlay = findViewById(R.id.connectOverlay)
        statsOverlay = findViewById(R.id.statsOverlay)
        disconnectBtn = findViewById(R.id.disconnectBtn)

        tabWifi = findViewById(R.id.tabWifi)
        tabUsb = findViewById(R.id.tabUsb)
        panelWifi = findViewById(R.id.panelWifi)
        panelUsb = findViewById(R.id.panelUsb)

        deviceList = findViewById(R.id.deviceList)
        noDevices = findViewById(R.id.noDevices)
        ipInput = findViewById(R.id.ipInput)
        portInput = findViewById(R.id.portInput)
        connectBtn = findViewById(R.id.connectBtn)
        ipSpinner = findViewById(R.id.ipSpinner)

        connectUsbBtn = findViewById(R.id.connectUsbBtn)
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
                showIpDropdown(host.ips, host.port)
            }
        }

        // IP 下拉选择适配器
        ipSpinnerAdapter = ArrayAdapter(this, android.R.layout.simple_spinner_dropdown_item, ipList)
        ipSpinner.adapter = ipSpinnerAdapter
        ipSpinner.onItemSelectedListener = object : AdapterView.OnItemSelectedListener {
            override fun onItemSelected(parent: AdapterView<*>?, view: View?, position: Int, id: Long) {
                if (position < ipList.size) {
                    ipInput.setText(ipList[position])
                }
            }
            override fun onNothingSelected(parent: AdapterView<*>?) {}
        }
    }

    /**
     * 显示 IP 下拉选择框
     * IPv4 排前面，IPv6 排后面
     */
    private fun showIpDropdown(ips: List<String>, port: Int) {
        ipList.clear()

        // 分离 IPv4 和 IPv6，IPv4 在前
        val ipv4 = ips.filter { !it.contains(":") }
        val ipv6 = ips.filter { it.contains(":") }
        ipList.addAll(ipv4)
        ipList.addAll(ipv6)

        if (ipList.isNotEmpty()) {
            ipInput.setText(ipList[0])
            portInput.setText(port.toString())
        }

        ipSpinnerAdapter.notifyDataSetChanged()
        ipSpinner.visibility = if (ipList.size > 1) View.VISIBLE else View.GONE
    }

    // ==================== 模式切换 ====================

    private fun setupTabs() {
        val tabClick = View.OnClickListener { view ->
            when (view.id) {
                R.id.tabWifi -> switchMode("wifi")
                R.id.tabUsb -> switchMode("usb")
            }
        }
        tabWifi.setOnClickListener(tabClick)
        tabUsb.setOnClickListener(tabClick)
    }

    private fun switchMode(mode: String) {
        currentMode = mode

        val inactiveColor = "#AAAAAA"
        val activeColor = "#FFFFFF"
        val activeBg = "#FF6200EE"
        val inactiveBg = "#333333"

        listOf(tabWifi, tabUsb).forEach {
            it.setTextColor(android.graphics.Color.parseColor(inactiveColor))
            it.setBackgroundColor(android.graphics.Color.parseColor(inactiveBg))
        }

        panelWifi.visibility = View.GONE
        panelUsb.visibility = View.GONE

        when (mode) {
            "wifi" -> {
                tabWifi.setTextColor(android.graphics.Color.parseColor(activeColor))
                tabWifi.setBackgroundColor(android.graphics.Color.parseColor(activeBg))
                panelWifi.visibility = View.VISIBLE
                discoveryClient?.requestDiscovery()
            }
            "usb" -> {
                tabUsb.setTextColor(android.graphics.Color.parseColor(activeColor))
                tabUsb.setBackgroundColor(android.graphics.Color.parseColor(activeBg))
                panelUsb.visibility = View.VISIBLE
            }
        }
    }

    // ==================== 按钮事件 ====================

    private fun setupButtons() {
        connectBtn.setOnClickListener {
            val host = ipInput.text.toString().trim()
            val port = portInput.text.toString().trim().toIntOrNull() ?: 4747
            if (host.isEmpty()) {
                ipInput.error = "请输入主机 IP"
                return@setOnClickListener
            }
            connect(host, port)
        }

        connectUsbBtn.setOnClickListener {
            val port = portInput.text.toString().trim().toIntOrNull() ?: 4747
            connect("127.0.0.1", port)
        }

        settingsBtn.setOnClickListener {
            if (!isConnected) {
                showSettingsDialog()
            }
        }

        disconnectBtn.setOnClickListener {
            disconnect()
        }
    }

    private fun showSettingsDialog() {
        val dialog = SettingsDialog(this, currentSettings) { newSettings ->
            currentSettings = newSettings
        }
        dialog.show()
    }

    // ==================== 悬浮球菜单 ====================

    private fun setupFloatingBubble() {
        val bubble = ImageButton(this)
        bubble.setImageResource(android.R.drawable.ic_menu_more)
        bubble.setBackgroundColor(android.graphics.Color.parseColor("#88000000"))
        bubble.scaleType = ImageView.ScaleType.CENTER_INSIDE
        bubble.setPadding(12, 12, 12, 12)

        val params = android.widget.FrameLayout.LayoutParams(56.dpToPx(), 56.dpToPx())
        params.gravity = (android.view.Gravity.BOTTOM or android.view.Gravity.END)
        params.setMargins(0, 0, 16.dpToPx(), 80.dpToPx())
        bubble.layoutParams = params
        bubble.visibility = View.GONE
        bubble.elevation = 10f

        (findViewById<android.widget.FrameLayout>(android.R.id.content)).addView(bubble)

        bubble.setOnClickListener {
            toggleFloatingMenu()
        }

        floatingBubble = bubble
    }

    private fun toggleFloatingMenu() {
        isMenuVisible = !isMenuVisible
        if (isMenuVisible) {
            showFloatingMenu()
        } else {
            hideFloatingMenu()
        }
    }

    private fun showFloatingMenu() {
        val menuItems = listOf(
            "画质设置" to {
                val dialog = SettingsDialog(this, currentSettings) { s ->
                    currentSettings = s
                    reconnect()
                }
                dialog.show()
                isMenuVisible = false
            },
            "编码器: ${if (currentSettings.useHardwareEncoder) "硬件" else "软件"}" to {
                currentSettings = currentSettings.copy(
                    useHardwareEncoder = !currentSettings.useHardwareEncoder
                )
                Toast.makeText(this,
                    if (currentSettings.useHardwareEncoder) "硬件编码 (NVIDIA)" else "软件编码 (x264)",
                    Toast.LENGTH_SHORT).show()
                reconnect()
            },
            "断开连接" to { disconnect() }
        )

        val popup = PopupMenu(this, floatingBubble)
        menuItems.forEach { (label, _) -> popup.menu.add(label) }
        popup.setOnMenuItemClickListener { item ->
            val idx = menuItems.indexOfFirst { it.first == item.title.toString() }
            if (idx >= 0) {
                menuItems[idx].second.invoke()
                true
            } else false
        }
        popup.setOnDismissListener { isMenuVisible = false }
        popup.show()
    }

    private fun hideFloatingMenu() {
        isMenuVisible = false
    }

    private fun reconnect() {
        if (streamClient == null && !isConnected) return
        val host = connectedHost
        val port = connectedPort
        disconnect()
        mainHandler.postDelayed({ connect(host, port) }, 500)
    }

    private fun Int.dpToPx(): Int =
        (this * resources.displayMetrics.density).toInt()

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
                val ipv4 = d.ips.filter { !it.contains(":") }
                val ipv6 = d.ips.filter { it.contains(":") }
                val ipSummary = if (ipv4.isNotEmpty()) ipv4[0] else (ipv6.firstOrNull() ?: "?")
                deviceAdapter.add("${d.label}\n  $ipSummary:${d.port}")
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

            // ========== 直接触摸模式 ==========
            val nx = (event.x / width).coerceIn(0f, 1f)
            val ny = (event.y / height).coerceIn(0f, 1f)

            when (event.actionMasked) {
                MotionEvent.ACTION_DOWN -> {
                    touchDown = true
                    isLongPress = false
                    inputSender.sendMove(nx, ny)
                    // 启动长按检测
                    longPressRunnable = Runnable {
                        if (touchDown) {
                            isLongPress = true
                            inputSender.sendButton(nx, ny, 2, 1)  // 右键按下
                        }
                    }
                    mainHandler.postDelayed(longPressRunnable!!, LONG_PRESS_MS)
                }
                MotionEvent.ACTION_MOVE -> {
                    if (touchDown) inputSender.sendMove(nx, ny)
                }
                MotionEvent.ACTION_UP -> {
                    // 取消长按检测
                    longPressRunnable?.let { mainHandler.removeCallbacks(it) }
                    if (isLongPress) {
                        inputSender.sendButton(nx, ny, 2, 0)  // 右键释放
                    } else {
                        inputSender.sendButton(nx, ny, 1, 1)  // 左键按下
                        inputSender.sendButton(nx, ny, 1, 0)  // 左键释放
                    }
                    touchDown = false
                    isLongPress = false
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
        connectedHost = host
        connectedPort = port

        val bitrateKbps = calculateBitrate(
            currentSettings.width, currentSettings.height,
            currentSettings.fps, currentSettings.qualityMultiplier
        )

        val settings = StreamClient.ConnectionSettings(
            width = currentSettings.width,
            height = currentSettings.height,
            fps = currentSettings.fps,
            bitrateKbps = bitrateKbps,
            codec = "h264",
            screenWidth = screenWidthPx,
            screenHeight = screenHeightPx,
            displayMode = currentSettings.displayMode,
            useHardwareEncoder = currentSettings.useHardwareEncoder
        )

        connectOverlay.visibility = View.GONE
        statsOverlay.visibility = View.VISIBLE
        disconnectBtn.visibility = View.VISIBLE
        statsResolution.text = "连接中..."

        streamClient = StreamClient(host, port, settings, object : StreamClient.ClientCallback {
            override fun onConnected(params: StreamClient.SessionParams) {
                mainHandler.post {
                    isConnected = true
                    startStatsUpdates()
                    startDecoder(params)
                    floatingBubble?.visibility = View.VISIBLE
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

            override fun onBitrateChanged(bitrateKbps: Int) {
                mainHandler.post {
                    val mbps = bitrateKbps / 1000f
                    statsBitrate.text = "${"%.2f".format(mbps)} Mbps"
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
            width = params.width,
            height = params.height,
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

        // 延迟 ping 改为每 2 秒一次
        pingRunnable = object : Runnable {
            override fun run() {
                if (isConnected) {
                    streamClient?.sendPing()
                    mainHandler.postDelayed(this, 2000)
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
        floatingBubble?.visibility = View.GONE
        isMenuVisible = false
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

    /**
     * 计算目标码率。基准码率表与服务端 config.get_bitrate_kbps 保持一致，
     * 画质倍率在此基础上缩放（服务端会尊重客户端码率，上限为基准 3 倍）。
     */
    private fun calculateBitrate(width: Int, height: Int, fps: Int, quality: Float): Int {
        val effectiveFps = if (fps == 0) 144 else fps
        val baseKbps = when (Triple(width, height, effectiveFps)) {
            // 720p
            Triple(1280, 720, 60) -> 6000
            Triple(1280, 720, 90) -> 8000
            Triple(1280, 720, 120) -> 10000
            Triple(1280, 720, 144) -> 12000
            // 1080p
            Triple(1920, 1080, 60) -> 10000
            Triple(1920, 1080, 90) -> 14000
            Triple(1920, 1080, 120) -> 18000
            Triple(1920, 1080, 144) -> 21600
            // 2K (1440p)
            Triple(2560, 1440, 60) -> 18000
            Triple(2560, 1440, 90) -> 24000
            Triple(2560, 1440, 120) -> 32000
            Triple(2560, 1440, 144) -> 38400
            else -> {
                // 非预设分辨率（如按设备比例适配的高度）：与服务端 fallback 公式一致，
                // 以 720p@60 为基准，按像素数和帧率线性缩放。
                val ratio = (width.toFloat() * height.toFloat()) / (1280f * 720f) * (effectiveFps.toFloat() / 60f)
                (6000f * ratio).toInt()
            }
        }
        return (baseKbps * quality).toInt()
    }
}
