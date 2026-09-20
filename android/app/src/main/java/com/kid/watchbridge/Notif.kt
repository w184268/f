package com.kid.watchbridge

import android.Manifest
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build

/**
 * 通知小工具。
 *
 * 为什么要自己包这一层：原来的写法依赖 androidx 的 ContextCompat / NotificationCompat /
 * ActivityCompat 三个类。把它们去掉之后，这个 App 就**只依赖 Android 系统自带的 API**，
 * 编译时不再需要从 Maven 下载任何第三方库（androidx.core 那套 aar）。
 *
 * 好处很直接：在没网、被墙、代理不稳的机器上也能编出 APK；Gradle 首次构建时间也短一截。
 * 行为与原来完全一致 —— 只是把兼容性判断挪到了明面上。
 */
object Notif {

    const val CHANNEL = "watchbridge"

    /** 通知渠道：Android 8.0（API 26）起才有，本项目 minSdk=26，其实可以直接建，留着判断更稳。 */
    fun ensureChannel(ctx: Context, name: String = "手表视频投递") {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return
        val nm = ctx.getSystemService(NotificationManager::class.java) ?: return
        if (nm.getNotificationChannel(CHANNEL) == null) {
            nm.createNotificationChannel(
                NotificationChannel(CHANNEL, name, NotificationManager.IMPORTANCE_LOW)
            )
        }
    }

    /** 普通通知（已就绪、待发送等），带标题、正文、点击跳转，可选一个动作按钮。 */
    fun build(
        ctx: Context,
        title: String,
        text: String,
        contentIntent: PendingIntent?,
        high: Boolean = true,
        actionIcon: Int = 0,
        actionTitle: String? = null,
        actionIntent: PendingIntent? = null,
    ): Notification {
        val b = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            Notification.Builder(ctx, CHANNEL)
        } else {
            @Suppress("DEPRECATION")
            Notification.Builder(ctx)
        }

        b.setSmallIcon(android.R.drawable.stat_sys_download_done)
            .setContentTitle(title)
            .setContentText(text)
            .setAutoCancel(true)
            .setWhen(System.currentTimeMillis())

        if (contentIntent != null) b.setContentIntent(contentIntent)

        // BigTextStyle 是系统自带的，这里不需要 androidx
        @Suppress("DEPRECATION")
        b.setStyle(Notification.BigTextStyle().bigText(text))

        @Suppress("DEPRECATION")
        b.setPriority(if (high) Notification.PRIORITY_HIGH else Notification.PRIORITY_LOW)

        if (actionTitle != null && actionIntent != null) {
            b.addAction(if (actionIcon != 0) actionIcon else android.R.drawable.ic_menu_share,
                actionTitle, actionIntent)
        }
        return b.build()
    }

    /** 常驻通知：App 在前台服务里跑着，用它告诉用户"我还活着"。 */
    fun alive(ctx: Context, text: String, contentIntent: PendingIntent?): Notification {
        ensureChannel(ctx)
        val b = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            Notification.Builder(ctx, CHANNEL)
        } else {
            @Suppress("DEPRECATION")
            Notification.Builder(ctx)
        }
        b.setSmallIcon(android.R.drawable.stat_notify_sync)
            .setContentTitle("WatchBridge 正在运行")
            .setContentText(text)
            .setOngoing(true)
            .setAutoCancel(false)
        if (contentIntent != null) b.setContentIntent(contentIntent)
        @Suppress("DEPRECATION")
        b.setPriority(Notification.PRIORITY_LOW)
        return b.build()
    }

    /** 有没有通知权限。Android 13（API 33）起才需要运行时申请，之前一律视作有。 */
    fun canPost(ctx: Context): Boolean =
        Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU ||
            ctx.checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) ==
            PackageManager.PERMISSION_GRANTED

    /** 申请通知权限。用系统自带的 requestPermissions，不再需要 ActivityCompat。 */
    fun requestPost(activity: android.app.Activity, reqCode: Int = 7) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU && !canPost(activity)) {
            activity.requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), reqCode)
        }
    }

    /** 跳到系统的通知监听（消息读取）设置页。 */
    fun openNotifyListenerSettings(ctx: Context) {
        ctx.startActivity(
            Intent("android.settings.ACTION_NOTIFICATION_LISTENER_SETTINGS")
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        )
    }
}
