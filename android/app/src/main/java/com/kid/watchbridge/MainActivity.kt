package com.kid.watchbridge

import android.Manifest
import android.app.Activity
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.PowerManager
import android.provider.Settings
import android.text.method.ScrollingMovementMethod
import android.widget.Button
import android.widget.EditText
import android.widget.Switch
import android.widget.TextView
import android.widget.Toast
import kotlin.concurrent.thread

class MainActivity : Activity() {

    private lateinit var tvLog: TextView
    private lateinit var tvPerm: TextView
    private val logHook: () -> Unit = { runOnUiThread { renderLog() } }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        Prefs.init(this)
        setContentView(R.layout.activity_main)

        val etBase = findViewById<EditText>(R.id.etBase)
        val etKey = findViewById<EditText>(R.id.etKey)
        val etPkg = findViewById<EditText>(R.id.etPkg)
        val swDry = findViewById<Switch>(R.id.swDry)
        val swAuto = findViewById<Switch>(R.id.swAuto)
        tvLog = findViewById(R.id.tvLog)
        tvPerm = findViewById(R.id.tvPerm)
        tvLog.movementMethod = ScrollingMovementMethod()

        etBase.setText(Prefs.baseUrl)
        etKey.setText(Prefs.apiKey)
        etPkg.setText(Prefs.watchPackage)
        swDry.isChecked = Prefs.dryRun
        swAuto.isChecked = Prefs.autoSend

        findViewById<Button>(R.id.btnSave).setOnClickListener {
            Prefs.baseUrl = etBase.text.toString()
            Prefs.apiKey = etKey.text.toString()
            Prefs.watchPackage = etPkg.text.toString()
            Prefs.dryRun = swDry.isChecked
            Prefs.autoSend = swAuto.isChecked
            toast("已保存")
            LogBus.add("配置已更新：${Prefs.baseUrl}")
            BridgeService.start(this)
            refreshPerm()
        }

        findViewById<Button>(R.id.btnTest).setOnClickListener {
            thread {
                val res = Api.health(Prefs.baseUrl, Prefs.apiKey)
                runOnUiThread {
                    toast(res.getOrNull() ?: "失败：${res.exceptionOrNull()?.message}")
                    LogBus.add(res.getOrNull() ?: "连接失败：${res.exceptionOrNull()?.message}")
                }
            }
        }

        findViewById<Button>(R.id.btnNotify).setOnClickListener {
            startActivity(Intent(Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS))
        }

        findViewById<Button>(R.id.btnA11y).setOnClickListener {
            startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS))
        }

        findViewById<Button>(R.id.btnBattery).setOnClickListener {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
                val i = Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS)
                    .setData(Uri.parse("package:$packageName"))
                try { startActivity(i) } catch (_: Exception) {}
            }
        }

        findViewById<Button>(R.id.btnPull).setOnClickListener {
            BridgeService.start(this)
            startService(Intent(this, BridgeService::class.java)
                .setAction(BridgeService.ACTION_POLL_NOW))
            toast("正在取件…")
        }

        findViewById<Button>(R.id.btnTrial).setOnClickListener {
            thread {
                val res = Api.ingest(Prefs.baseUrl, Prefs.apiKey, Prefs.senderName, "为什么天空是蓝色的？")
                runOnUiThread {
                    toast(if (res.isSuccess) "已提交测试提问，约 30 秒后出片" else "提交失败")
                    LogBus.add("测试提问已提交")
                }
            }
        }

        findViewById<Button>(R.id.btnClear).setOnClickListener {
            LogBus.clear()
            renderLog()
        }

        LogBus.onChange(logHook)
        requestNotifyPermission()
        BridgeService.start(this)
        refreshPerm()
        renderLog()
    }

    override fun onResume() {
        super.onResume()
        refreshPerm()
        renderLog()
    }

    private fun refreshPerm() {
        val flat = Settings.Secure.getString(contentResolver, "enabled_notification_listeners")
        val self = ComponentName(this, ChatListener::class.java).flattenToString()
        val notifyOk = flat?.contains(self) == true

        val a11yOk = SendService.isConnected()
        val batteryOk = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
            (getSystemService(Context.POWER_SERVICE) as PowerManager)
                .isIgnoringBatteryOptimizations(packageName)
        } else true

        tvPerm.text = buildString {
            append(if (notifyOk) "✅ 消息监听已授权" else "❌ 消息监听未授权（下面按钮开启）").append('\n')
            append(if (a11yOk) "✅ 无障碍已开启" else "⚠️ 无障碍未开启（自动发送需要）").append('\n')
            append(if (batteryOk) "✅ 已在电池白名单" else "⚠️ 未加白名单，后台可能被杀")
        }
    }

    private fun requestNotifyPermission() {
        Notif.requestPost(this)
    }

    private fun renderLog() {
        tvLog.text = LogBus.snapshot().ifEmpty { "还没有日志。" }
    }

    private fun toast(msg: String) =
        Toast.makeText(this, msg, Toast.LENGTH_SHORT).show()
}
