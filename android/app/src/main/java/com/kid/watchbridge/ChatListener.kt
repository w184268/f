package com.kid.watchbridge

import android.app.Notification
import android.os.Bundle
import android.service.notification.NotificationListenerService
import android.service.notification.StatusBarNotification
import kotlin.concurrent.thread

/**
 * 监听手表 App 的通知，把聊天正文转发给服务端。
 *
 * 为什么要过滤：小天才除了微聊消息，还会推"低电量/到校/安全区域"等系统提醒，
 * 这些不必触发找视频，否则孩子会收到莫名其妙的回复。
 */
class ChatListener : NotificationListenerService() {

    override fun onListenerConnected() {
        LogBus.add("消息监听已连接")
    }

    override fun onListenerDisconnected() {
        LogBus.add("消息监听断开（检查是否被系统省电策略杀掉）")
    }

    override fun onNotificationPosted(sbn: StatusBarNotification?) {
        val sbn = sbn ?: return
        val pkg = sbn.packageName ?: return
        if (pkg !in Prefs.watchPackages()) return

        val extras: Bundle = sbn.notification?.extras ?: return
        val title = extras.getCharSequence(Notification.EXTRA_TITLE)?.toString().orEmpty()
        val bigText = extras.getCharSequence(Notification.EXTRA_BIG_TEXT)?.toString()
        val text = extras.getCharSequence(Notification.EXTRA_TEXT)?.toString()
        val lines = extras.getCharSequenceArray(Notification.EXTRA_TEXT_LINES)
            ?.joinToString(" ") { it?.toString().orEmpty() }

        val content = (bigText ?: lines ?: text ?: "").trim()
        if (content.isEmpty()) {
            LogBus.add("忽略空正文通知：标题=${title.take(20)} 渠道=${sbn.notification?.channelId}")
            return
        }

        // 渠道白名单
        val wl = Prefs.channelWhitelist.split(",").map { it.trim() }.filter { it.isNotEmpty() }
        val channel = sbn.notification?.channelId ?: ""
        if (wl.isNotEmpty() && wl.none { channel.contains(it, true) }) {
            LogBus.add("渠道不匹配已忽略：$channel")
            return
        }

        // 关键词黑名单
        val hit = Prefs.blockWords.split(",")
            .map { it.trim() }.filter { it.isNotEmpty() }
            .firstOrNull { content.contains(it, true) || title.contains(it, true) }
        if (hit != null) {
            LogBus.add("忽略系统提醒（命中「$hit」）：${content.take(30)}")
            return
        }

        // 短时去重：同一条内容可能被系统多次投递
        val now = System.currentTimeMillis()
        val key = (title + "|" + content).hashCode()
        lastTs[key]?.let { if (now - it < 8000) return }
        lastTs[key] = now

        LogBus.add("捕获消息：${title.take(12)} → ${content.take(40)}")

        val sender = title.ifBlank { Prefs.senderName }
        thread(name = "ingest") {
            val res = Api.ingest(Prefs.baseUrl, Prefs.apiKey, sender, content)
            val id = res.getOrNull()
            LogBus.add(
                if (res.isSuccess && id != null && id >= 0) "已提交服务端 taskId=$id"
                else if (res.isSuccess) "服务端判定为重复消息，已跳过"
                else "提交失败：${res.exceptionOrNull()?.message}"
            )
        }
    }

    companion object {
        private val lastTs = HashMap<Int, Long>(64)
    }
}
