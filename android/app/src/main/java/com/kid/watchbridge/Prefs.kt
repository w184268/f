package com.kid.watchbridge

import android.content.Context
import android.content.SharedPreferences
import java.util.LinkedList

/**
 * 轻量配置存储。
 * 用 apply() 异步落盘，避免主线程卡 IO。
 */
object Prefs {
    private const val FILE = "watchbridge"
    private lateinit var sp: SharedPreferences

    fun init(ctx: Context) {
        sp = ctx.getSharedPreferences(FILE, Context.MODE_PRIVATE)
    }

    var baseUrl: String
        get() = sp.getString("base_url", "http://192.168.1.10:8787") ?: ""
        set(v) = sp.edit().putString("base_url", v.trimEnd('/')).apply()

    var apiKey: String
        get() = sp.getString("api_key", "change-me-please") ?: ""
        set(v) = sp.edit().putString("api_key", v.trim()).apply()

    /** 手表端 App 包名，小天才默认是 com.xtc.watch */
    var watchPackage: String
        get() = sp.getString("watch_pkg", "com.xtc.watch") ?: "com.xtc.watch"
        set(v) = sp.edit().putString("watch_pkg", v.trim()).apply()

    var senderName: String
        get() = sp.getString("sender", "孩子") ?: "孩子"
        set(v) = sp.edit().putString("sender", v).apply()

    /** 轮询间隔（毫秒） */
    var pollIntervalMs: Long
        get() = sp.getLong("poll_ms", 20_000L)
        set(v) = sp.edit().putLong("poll_ms", v).apply()

    /** 是否尝试用无障碍自动发送 */
    var autoSend: Boolean
        get() = sp.getBoolean("auto_send", false)
        set(v) = sp.edit().putBoolean("auto_send", v).apply()

    /** 干跑模式：只 dump 界面不真点击，配步进提交 jungle 用 */
    var dryRun: Boolean
        get() = sp.getBoolean("dry_run", true)
        set(v) = sp.edit().putBoolean("dry_run", v).apply()

    /** 只处理命中包含这些词的通知渠道，留空表示不限渠道 */
    var channelWhitelist: String
        get() = sp.getString("chan_wl", "") ?: ""
        set(v) = sp.edit().putString("chan_wl", v).apply()

    /** 通知里含这些词的一律忽略（手表端系统提醒） */
    var blockWords: String
        get() = sp.getString("block_words", DEFAULT_BLOCK) ?: DEFAULT_BLOCK
        set(v) = sp.edit().putString("block_words", v).apply()

    /** 自动发送的点击脚本 */
    var sendSteps: String
        get() = sp.getString("send_steps", DEFAULT_STEPS) ?: DEFAULT_STEPS
        set(v) = sp.edit().putString("send_steps", v).apply()

    const val DEFAULT_BLOCK = "电量,低电量,定位,到校,离校,安全区域,围栏,SOS,摔倒,朋友圈,点赞,升级,欠费,流量,关机"

    /**
     * 默认的 micro 交互脚本。每个 App 版本 UI 不同，用「干跑模式」看节点树后微调最稳。
     * 动作含义见 SendService.execStep
     */
    const val DEFAULT_STEPS = """
[
  {"a":"wait","v":"3000"},
  {"a":"tap_any","v":"微聊|消息|聊天"},
  {"a":"wait","v":"1500"},
  {"a":"tap_any","v":"更多|加号|更多功能|+"},
  {"a":"wait","v":"1200"},
  {"a":"tap_any","v":"视频|相册|图片|照片|文件"},
  {"a":"wait","v":"1800"},
  {"a":"tap_first_media"},
  {"a":"wait","v":"1500"},
  {"a":"tap_any","v":"发送|完成|确定|打开"},
  {"a":"wait","v":"4000"}
]
"""

    fun watchPackages(): List<String> =
        watchPackage.split(",").map { it.trim() }.filter { it.isNotEmpty() }
            .ifEmpty { listOf("com.xtc.watch") }
}

/** 内存日志总线：界面、通知里都要看得到同一份日志。 */
object LogBus {
    private val lines = LinkedList<String>()
    private const val MAX = 300
    private val listeners = mutableListOf<() -> Unit>()

    fun add(msg: String) {
        val stamp = java.text.SimpleDateFormat("HH:mm:ss", java.util.Locale.CHINA)
            .format(java.util.Date())
        synchronized(lines) {
            lines.addFirst("[$stamp] $msg")
            while (lines.size > MAX) lines.removeLast()
        }
        listeners.forEach { try { it() } catch (_: Throwable) {} }
    }

    fun snapshot(): String = synchronized(lines) { lines.joinToString("\n") }

    fun clear() = synchronized(lines) { lines.clear() }

    fun onChange(l: () -> Unit) { listeners.add(l) }
}
