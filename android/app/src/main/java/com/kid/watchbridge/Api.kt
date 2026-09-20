package com.kid.watchbridge

import android.os.Build
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.io.FileOutputStream
import java.net.HttpURLConnection
import java.net.URL

/**
 * 极小网络层：只用 HttpURLConnection + org.json，零第三方依赖。
 * 所有方法都是阻塞的，调用方请放到后台线程。
 */
object Api {

    data class OutItem(
        val taskId: Int,
        val kind: String,
        val replyText: String,
        val sourceTitle: String?,
        val sourceAuthor: String?,
        val sourceUrl: String?,
        val videoUrl: String?,
        val coverUrl: String?,
        val size: Long,
    )

    class Err(msg: String) : Exception(msg)

    private fun conn(url: String, key: String, method: String = "GET"): HttpURLConnection {
        val c = URL(url).openConnection() as HttpURLConnection
        c.requestMethod = method
        c.connectTimeout = 8_000
        c.readTimeout = 20_000
        c.setRequestProperty("X-Api-Key", key)
        c.setRequestProperty("Accept", "application/json")
        c.setRequestProperty("User-Agent", "WatchBridge/1.0")
        return c
    }

    fun health(base: String, key: String): Result<String> = try {
        val c = conn("$base/api/v1/health", key)
        val code = c.responseCode
        val body = if (code in 200..299) c.inputStream.readText() else c.errorStream?.readText().orEmpty()
        c.disconnect()
        if (code in 200..299) {
            val o = JSONObject(body)
            Result.success(
                "连通正常 · 理解引擎=${o.optString("understanding")} · 审核=${o.optString("mode")}"
            )
        } else Result.failure(Err("HTTP $code ${body.take(120)}"))
    } catch (e: Exception) {
        Result.failure(e)
    }

    fun ingest(base: String, key: String, sender: String, text: String): Result<Int> = try {
        val c = conn("$base/api/v1/ingest", key, "POST")
        c.setRequestProperty("Content-Type", "application/json")
        val body = JSONObject()
            .put("sender", sender)
            .put("text", text)
            .put("device_id", Build.MODEL ?: "phone")
            .put("ts", System.currentTimeMillis() / 1000.0)
        c.outputStream.use { it.write(body.toString().toByteArray()) }
        val code = c.responseCode
        val resp = if (code in 200..299) c.inputStream.readText() else c.errorStream?.readText().orEmpty()
        c.disconnect()
        if (code in 200..299) {
            val o = JSONObject(resp)
            if (o.optBoolean("skipped", false)) Result.success(-1)
            else Result.success(o.optInt("task_id", -1))
        } else Result.failure(Err("HTTP $code"))
    } catch (e: Exception) {
        Result.failure(e)
    }

    fun outbox(base: String, key: String, limit: Int = 3): Result<List<OutItem>> = try {
        val c = conn("$base/api/v1/outbox?limit=$limit", key)
        val code = c.responseCode
        val body = if (code in 200..299) c.inputStream.readText() else c.errorStream?.readText().orEmpty()
        c.disconnect()
        if (code !in 200..299) Result.failure(Err("HTTP $code"))
        else {
            val arr = JSONObject(body).optJSONArray("items") ?: org.json.JSONArray()
            val out = mutableListOf<OutItem>()
            for (i in 0 until arr.length()) {
                val o = arr.getJSONObject(i)
                out.add(
                    OutItem(
                        taskId = o.optInt("task_id"),
                        kind = o.optString("kind", "text"),
                        replyText = o.optString("reply_text", ""),
                        sourceTitle = o.optString("source_title", "").ifBlank { null },
                        sourceAuthor = o.optString("source_author", "").ifBlank { null },
                        sourceUrl = o.optString("source_url", "").ifBlank { null },
                        videoUrl = o.optString("video_url", "").ifBlank { null },
                        coverUrl = o.optString("cover_url", "").ifBlank { null },
                        size = o.optLong("size", 0L),
                    )
                )
            }
            Result.success(out)
        }
    } catch (e: Exception) {
        Result.failure(e)
    }

    fun ack(base: String, key: String, taskId: Int, status: String, note: String = ""): Boolean = try {
        val c = conn("$base/api/v1/ack", key, "POST")
        c.setRequestProperty("Content-Type", "application/json")
        val body = JSONObject()
            .put("task_id", taskId).put("status", status).put("note", note)
        c.outputStream.use { it.write(body.toString().toByteArray()) }
        val ok = c.responseCode in 200..299
        c.disconnect()
        ok
    } catch (_: Exception) {
        false
    }

    /** 下载视频到本地文件。返回字节数。 */
    fun download(url: String, dest: File): Long = try {
        dest.parentFile?.mkdirs()
        val c = URL(url).openConnection() as HttpURLConnection
        c.connectTimeout = 10_000
        c.readTimeout = 60_000
        c.setRequestProperty("User-Agent", "WatchBridge/1.0")
        if (c.responseCode !in 200..299) throw Err("HTTP ${c.responseCode}")
        var total = 0L
        c.inputStream.use { input ->
            FileOutputStream(dest).use { out ->
                val buf = ByteArray(64 * 1024)
                while (true) {
                    val n = input.read(buf)
                    if (n <= 0) break
                    out.write(buf, 0, n)
                    total += n
                }
            }
        }
        c.disconnect()
        total
    } catch (e: Exception) {
        throw e
    }
}
