package com.kid.watchbridge

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent

/** 开机 / 升级后自动把桥接服务拉起来，省得每次手动打开 App。 */
class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        when (intent.action) {
            Intent.ACTION_BOOT_COMPLETED,
            Intent.ACTION_MY_PACKAGE_REPLACED -> {
                Prefs.init(context)
                BridgeService.start(context)
            }
        }
    }
}
