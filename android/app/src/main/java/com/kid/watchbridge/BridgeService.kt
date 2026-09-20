package com.kid.watchbridge

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.ContentValues
import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.Environment
import android.os.Handler
import android.os.HandlerThread
import android.os.IBinder
import android.provider.MediaStore
import java.io.File
import java.io.FileOutputStream

/**
 * 前台常驻服务：定时向服务端取件 → 下载视频 → 存入系统相册 → 通知/自动投递到手表。
 *
 * 为什么不用 WorkManager：WorkManager 最短周期 15 分钟，孩子等不了。
 * 这里用前台服务 + Handler 轮询（默认 20 秒），并在省电白名单里更好存活。
 */
class BridgeService : Service() {

    private lateinit var worker: HandlerThread
    private lateinit var handler: Handler
    private var stopping = false

    override fun onCreate() {
        super.onCreate()
        Prefs.init(this)
        worker = HandlerThread("bridge-poll").also { it.start() }
        handler = Handler(worker.looper)
        startForeground(NOTI_ALIVE, aliveNotification("正在等待孩子的提问…"))
        LogBus.add("桥接服务已启动")
        schedule()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_POLL_NOW) {
            LogBus.add("手动取件")
            handler.post { pollOnce() }
        } else if (intent?.action == ACTION_DELIVER) {
            val taskId = intent.getIntExtra(EXTRA_TASK_ID, -1)
            val path = intent.getStringExtra(EXTRA_FILE)
            if (taskId > 0 && path != null) {
                LogBus.add("收到投递指令 taskId=$taskId")
                handler.post { deliver(taskId, File(path)) }
            }
        }
        return START_STICKY
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onDestroy() {
        stopping = true
        handler.removeCallbacksAndMessages(null)
        worker.quitSafely()
        LogBus.add("桥接服务已停止")
        super.onDestroy()
    }

    private fun schedule() {
        handler.postDelayed({
            if (stopping) return@postDelayed
            try { pollOnce() } catch (e: Throwable) { LogBus.add("取件异常：${e.message}") }
            schedule()
        }, Prefs.pollIntervalMs)
    }

    // ------------------------------------------------------------ 取件
    private fun pollOnce() {
        val items = Api.outbox(Prefs.baseUrl, Prefs.apiKey).getOrNull()
        if (items == null) {
            LogBus.add("取件失败（服务端不可达？）")
            return
        }
        if (items.isEmpty()) return

        for (item in items) {
            LogBus.add("取到任务 ${item.taskId} 类型=${item.kind}")
            if (item.kind == "video" && !item.videoUrl.isNullOrEmpty()) {
                handleVideo(item)
            } else {
                notifyReady(item, null)
                Api.ack(Prefs.baseUrl, Prefs.apiKey, item.taskId, "delivered", "text-only")
            }
        }
    }

    private fun handleVideo(item: Api.OutItem) {
        val local = File(filesDir, "videos").apply { mkdirs() }
            .let { File(it, "wt_${item.taskId}.mp4") }
        try {
            val bytes = Api.download(item.videoUrl!!, local)
            LogBus.add("下载完成 ${bytes / 1024}KB → ${local.name}")
        } catch (e: Throwable) {
            LogBus.add("下载失败：${e.message}")
            Api.ack(Prefs.baseUrl, Prefs.apiKey, item.taskId, "failed", "download failed")
            return
        }

        val publicUri = saveToGallery(local, item)
        if (publicUri == null) {
            LogBus.add("写入相册失败，仍尝试直接投递")
        }

        notifyReady(item, local.absolutePath)

        if (Prefs.autoSend && SendService.isConnected()) {
            LogBus.add("自动发送已开启，正在投递…")
            deliver(item.taskId, local)
        }
    }

    /** 把视频写进 Movies/WatchTube，让小天才的相册选择器能看到它。 */
    private fun saveToGallery(src: File, item: Api.OutItem): android.net.Uri? {
        return try {
            val name = "WatchTube_${item.taskId}_${System.currentTimeMillis()}.mp4"
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                val values = ContentValues().apply {
                    put(MediaStore.Video.Media.DISPLAY_NAME, name)
                    put(MediaStore.Video.Media.MIME_TYPE, "video/mp4")
                    put(MediaStore.Video.Media.RELATIVE_PATH,
                        Environment.DIRECTORY_MOVIES + "/WatchTube")
                    put(MediaStore.Video.Media.IS_PENDING, 1)
                }
                val uri = contentResolver.insert(
                    MediaStore.Video.Media.EXTERNAL_CONTENT_URI, values) ?: return null
                contentResolver.openOutputStream(uri)?.use { out ->
                    src.inputStream().use { it.copyTo(out) }
                }
                values.clear()
                values.put(MediaStore.Video.Media.IS_PENDING, 0)
                contentResolver.update(uri, values, null, null)
                LogBus.add("已写入相册：$name")
                uri
            } else {
                @Suppress("DEPRECATION")
                val dir = File(Environment.getExternalStoragePublicDirectory(
                    Environment.DIRECTORY_MOVIES), "WatchTube")
                dir.mkdirs()
                val file = File(dir, name)
                FileOutputStream(file).use { out -> src.inputStream().use { it.copyTo(out) } }
                // 触发媒体扫描
                sendBroadcast(Intent(Intent.ACTION_MEDIA_SCANNER_SCAN_FILE,
                    android.net.Uri.fromFile(file)))
                LogBus.add("已写入相册：$name")
                android.net.Uri.fromFile(file)
            }
        } catch (e: Throwable) {
            LogBus.add("写相册失败：${e.message}")
            null
        }
    }

    // ------------------------------------------------------------ 投递
    private fun deliver(taskId: Int, file: File) {
        if (!file.exists()) {
            LogBus.add("文件不存在，投递中止")
            return
        }
        val ok = SendService.deliver(this, taskId)
        Api.ack(Prefs.baseUrl, Prefs.apiKey, taskId,
            if (ok) "delivered" else "failed", if (ok) "" else "a11y steps incomplete")
        LogBus.add(if (ok) "投递完成 ✨" else "投递未完成，请查看日志或手动发送")
    }

    // ------------------------------------------------------------ 通知
    private fun notifyReady(item: Api.OutItem, filePath: String?) {
        ensureChannel()
        val nm = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager

        val text = listOfNotNull(
            item.replyText.ifBlank { null },
            item.sourceTitle?.let { "《$it》" },
            item.sourceAuthor?.let { "by $it" }
        ).joinToString(" · ")

        val intent = Intent(this, BridgeService::class.java).apply {
            action = ACTION_DELIVER
            putExtra(EXTRA_TASK_ID, item.taskId)
            putExtra(EXTRA_FILE, filePath)
        }
        val pi = PendingIntent.getService(
            this, item.taskId, intent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)

        val open = PendingIntent.getActivity(
            this, item.taskId + 1000, Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)

        val noti = Notif.build(
            ctx = this,
            title = if (item.kind == "video") "视频已就绪" else "回复已就绪",
            text = text,
            contentIntent = open,
            high = true,
            actionTitle = if (!filePath.isNullOrEmpty() && item.kind == "video") "发给孩子" else null,
            actionIntent = pi,
        )
        nm.notify(item.taskId, noti)
    }

    private fun aliveNotification(text: String): Notification {
        ensureChannel()
        val open = PendingIntent.getActivity(
            this, 0, Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
        return Notif.alive(this, text, open)
    }

    private fun ensureChannel() = Notif.ensureChannel(this)

    companion object {
        const val CHANNEL = Notif.CHANNEL
        const val NOTI_ALIVE = 1001
        const val ACTION_POLL_NOW = "com.kid.watchbridge.POLL_NOW"
        const val ACTION_DELIVER = "com.kid.watchbridge.DELIVER"
        const val EXTRA_TASK_ID = "task_id"
        const val EXTRA_FILE = "file"

        fun start(ctx: Context) {
            Prefs.init(ctx)
            val i = Intent(ctx, BridgeService::class.java)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O)
                ctx.startForegroundService(i) else ctx.startService(i)
        }
    }
}
