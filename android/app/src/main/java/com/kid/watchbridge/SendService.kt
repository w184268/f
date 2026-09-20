package com.kid.watchbridge

import android.accessibilityservice.AccessibilityService
import android.accessibilityservice.GestureDescription
import android.content.Context
import android.content.Intent
import android.graphics.Path
import android.graphics.Rect
import android.os.Handler
import android.os.Looper
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo
import java.util.Locale
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

/**
 * 无障碍投递：在小天才 App 里完成「选视频 → 发送」这一串动作。
 *
 * 现实情况：不同版本的 App UI 不一样，任何自动点击都可能失手。
 * 所以第一步请用「干跑模式」：只把界面节点 dump 到日志、不真的点击，
 * 对着日志把 Script 调准，再关掉干跑。
 */
class SendService : AccessibilityService() {

    private val main = Handler(Looper.getMainLooper())

    override fun onServiceConnected() {
        instance = this
        LogBus.add("无障碍服务已连接")
    }

    override fun onDestroy() {
        instance = null
        super.onDestroy()
    }

    override fun onInterrupt() {}
    override fun onAccessibilityEvent(event: AccessibilityEvent?) {}

    // ------------------------------------------------------------ 对外
    companion object {
        @Volatile
        private var instance: SendService? = null

        fun isConnected(): Boolean = instance != null

        /**
         * 投递一个视频。阻塞最多 60 秒，返回脚本是否跑完。
         * true 只代表动作序列执行完毕，不代表孩子一定收到了（最终以服务端 ack 为准）。
         */
        fun deliver(ctx: Context, taskId: Int): Boolean {
            val svc = instance
            if (svc == null) {
                LogBus.add("无法自动发送：无障碍服务未开启")
                return false
            }
            return svc.runScript(taskId)
        }
    }

    // ------------------------------------------------------------ 脚本执行
    private fun runScript(taskId: Int): Boolean {
        val latch = CountDownLatch(1)
        var ok = false

        main.post {
            val steps = try {
                org.json.JSONArray(Prefs.sendSteps)
            } catch (e: Throwable) {
                LogBus.add("脚本解析失败：${e.message}")
                null
            }
            if (steps == null) {
                ok = false
                latch.countDown()
                return@post
            }

            // 先把手表 App 拉到前台
            bringWatchAppToFront()

            Thread {
                try {
                    for (i in 0 until steps.length()) {
                        val s = steps.getJSONObject(i)
                        val act = s.optString("a")
                        val v = s.optString("v", "")
                        if (!execStep(act, v)) {
                            LogBus.add("步骤 [$act $v] 未命中，dump 界面辅助排查")
                            dumpTree()
                        }
                    }
                    ok = true
                } catch (e: Throwable) {
                    LogBus.add("脚本异常：${e.message}")
                } finally {
                    latch.countDown()
                }
            }.start()
        }

        return latch.await(60, TimeUnit.SECONDS) && ok
    }

    private fun bringWatchAppToFront() {
        val pkg = Prefs.watchPackages().first()
        val intent = packageManager.getLaunchIntentForPackage(pkg)
        if (intent == null) {
            LogBus.add("未找到手表 App（包名：$pkg），请检查配置")
            return
        }
        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_RESET_TASK_IF_NEEDED)
        try {
            startActivity(intent)
            LogBus.add("已唤起 $pkg")
        } catch (e: Throwable) {
            LogBus.add("唤起失败：${e.message}")
        }
    }

    private fun root(): AccessibilityNodeInfo? =
        try { rootInActiveWindow } catch (_: Throwable) { null }

    /** 返回 false 表示该步骤没找到目标 */
    private fun execStep(act: String, v: String): Boolean {
        when (act) {
            "wait" -> {
                Thread.sleep(v.toLongOrNull() ?: 1000)
                return true
            }
            "back" -> return performGlobalAction(GLOBAL_ACTION_BACK)
            "home" -> return performGlobalAction(GLOBAL_ACTION_HOME)
            "scroll" -> {
                val node = findScrollable() ?: return false
                return node.performAction(AccessibilityNodeInfo.ACTION_SCROLL_FORWARD)
            }
            "dump" -> { dumpTree(); return true }
        }

        val node: AccessibilityNodeInfo? = when {
            act == "tap_first_media" -> findFirstMediaItem()
            else -> findAny(v)
        } ?: return false

        when (act) {
            "tap_any", "tap", "click", "tap_first_media" -> {
                if (Prefs.dryRun) {
                    LogBus.add("干跑：将点击「${describe(node)}」")
                    return true
                }
                return clickNode(node)
            }
        }
        return false
    }

    // ------------------------------------------------------------ 查找与点击
    private fun describe(n: AccessibilityNodeInfo): String {
        val t = n.text?.toString().orEmpty()
        val d = n.contentDescription?.toString().orEmpty()
        val id = n.viewIdResourceName?.substringAfterLast('/').orEmpty()
        return listOf(t, d, id).filter { it.isNotEmpty() }.joinToString("/")
    }

    /** 按 text 或 contentDescription 模糊匹配（| 分隔多个候选，大小写不敏感） */
    private fun findAny(pattern: String): AccessibilityNodeInfo? {
        val r = root() ?: return null
        val cands = pattern.split("|")
            .map { it.trim().lowercase(Locale.CHINA) }.filter { it.isNotEmpty() }
        if (cands.isEmpty()) return null
        return walk(r).firstOrNull { n ->
            val blob = (n.text?.toString().orEmpty() + " " +
                n.contentDescription?.toString().orEmpty()).lowercase(Locale.CHINA)
            cands.any { blob.contains(it) }
        }
    }

    /** 相册/选择器里的第一个媒体项：先定位可滚动列表，再取其第一个可点击子项 */
    private fun findFirstMediaItem(): AccessibilityNodeInfo? {
        val list = findScrollable() ?: return null
        if (list.childCount == 0) return null
        for (i in 0 until list.childCount) {
            val child = list.getChild(i) ?: continue
            val target = if (child.childCount > 0)
                walk(child).firstOrNull { it.isClickable } else child
            if (target != null) return target
        }
        return null
    }

    private fun findScrollable(): AccessibilityNodeInfo? =
        root()?.let { walk(it).firstOrNull { n -> n.isScrollable } }

    private fun walk(root: AccessibilityNodeInfo): Sequence<AccessibilityNodeInfo> = sequence {
        val stack = java.util.ArrayDeque<AccessibilityNodeInfo>()
        stack.add(root)
        var guard = 0
        while (stack.isNotEmpty() && guard++ < 400) {
            val n = stack.removeFirst()
            yield(n)
            for (i in 0 until n.childCount) {
                n.getChild(i)?.let { stack.add(it) }
            }
        }
    }

    private fun clickNode(node: AccessibilityNodeInfo): Boolean {
        // 1) 自己能点就点
        if (node.isClickable && !Prefs.dryRun) {
            if (node.performAction(AccessibilityNodeInfo.ACTION_CLICK)) {
                LogBus.add("已点击「${describe(node)}」")
                return true
            }
        }
        // 2) 往上找可点击祖先
        var p = node.parent
        var depth = 0
        while (p != null && depth++ < 5) {
            if (p.isClickable && p.performAction(AccessibilityNodeInfo.ACTION_CLICK)) {
                LogBus.add("已点击容器「${describe(p)}」")
                return true
            }
            p = p.parent
        }
        // 3) 都不行就用坐标手势（兜底）
        val rect = Rect()
        node.getBoundsInScreen(rect)
        if (rect.width() > 0 && rect.height() > 0) {
            val cx = rect.centerX().toFloat().coerceAtLeast(1f)
            val cy = rect.centerY().toFloat().coerceAtLeast(1f)
            val path = Path().apply { moveTo(cx, cy) }
            val okDis = dispatchGesture(
                GestureDescription.Builder()
                    .addStroke(GestureDescription.StrokeDescription(path, 0, 80))
                    .build(), null, null)
            if (okDis) {
                LogBus.add("用坐标手势点击 ($cx,$cy)「${describe(node)}」")
                return true
            }
        }
        LogBus.add("点击失败：${describe(node)}")
        return false
    }

    /** 把当前界面节点摘要打到日志，配脚本时靠它 */
    private fun dumpTree() {
        val r = root()
        if (r == null) {
            LogBus.add("dump：当前没有活动窗口")
            return
        }
        LogBus.add("──── 界面节点树开始 ────")
        walk(r).take(80).forEach { n ->
            val d = describe(n)
            if (d.isNotEmpty()) LogBus.add("  · $d${if (n.isClickable) " [可点击]" else ""}")
        }
        LogBus.add("──── 界面节点树结束 ────")
    }
}
