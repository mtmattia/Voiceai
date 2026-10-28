package app.voiceai

import android.content.Context
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.MultipartBody
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.asRequestBody
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import java.io.File
import java.io.IOException
import java.security.MessageDigest
import java.util.concurrent.TimeUnit

/** Client del server sul PC (via Tailscale). Chiamate bloccanti: usarle fuori dal main thread. */
class Api(ctx: Context) {
    private val prefs = Prefs(ctx)
    private val base = prefs.server
    private val json = "application/json".toMediaType()

    companion object {
        private val http = OkHttpClient.Builder()
            .connectTimeout(15, TimeUnit.SECONDS)
            .writeTimeout(5, TimeUnit.MINUTES)
            .readTimeout(5, TimeUnit.MINUTES)   // /api/ask può aspettare la fine di un blocco
            .build()

        fun sha256(f: File): String {
            val md = MessageDigest.getInstance("SHA-256")
            f.inputStream().use { s ->
                val buf = ByteArray(1 shl 16)
                while (true) { val n = s.read(buf); if (n < 0) break; md.update(buf, 0, n) }
            }
            return md.digest().joinToString("") { "%02x".format(it) }
        }
    }

    class HttpError(val code: Int, msg: String) : IOException(msg)

    private fun req(path: String) = Request.Builder().url(base + path).header("Authorization", "Bearer ${prefs.token}")

    private fun exec(r: Request, okCodes: Set<Int> = emptySet()): JSONObject {
        if (base.isEmpty()) throw IOException("server non configurato")
        http.newCall(r).execute().use { resp ->
            val body = resp.body?.string() ?: ""
            if (!resp.isSuccessful && resp.code !in okCodes) throw HttpError(resp.code, "HTTP ${resp.code}: ${body.take(200)}")
            return if (body.trimStart().startsWith("[")) JSONObject().put("items", org.json.JSONArray(body)) else JSONObject(body)
        }
    }

    /** -> risposta del server; ok=true e sha256 uguale = file salvato sul PC. 422 = checksum diverso. */
    fun upload(f: File, sha: String, session: String, idx: Int, startedAt: Double): JSONObject {
        val body = MultipartBody.Builder().setType(MultipartBody.FORM)
            .addFormDataPart("file", f.name, f.asRequestBody("audio/ogg".toMediaType()))
            .addFormDataPart("sha256", sha)
            .addFormDataPart("device", prefs.device)
            .addFormDataPart("session", session)
            .addFormDataPart("idx", idx.toString())
            .addFormDataPart("started_at", startedAt.toString())
            .build()
        return exec(req("/api/upload").post(body).build(), okCodes = setOf(422))
    }

    /** Abbinamento: codice a 6 cifre (da `voiceai abbina` sul PC) -> token. Senza autenticazione. */
    fun pair(code: String) = exec(Request.Builder().url("$base/api/pair")
        .post(JSONObject().put("code", code).toString().toRequestBody(json)).build()).getString("token")

    /** Qualche secondo della voce di una persona (Ogg/Opus) salvato in [dest]. */
    fun voice(pid: Long, dest: File) {
        if (base.isEmpty()) throw IOException("server non configurato")
        http.newCall(req("/api/persons/$pid/voice").get().build()).execute().use { r ->
            if (r.code == 404) throw HttpError(404, "nessuna frase abbastanza lunga da far sentire")
            if (!r.isSuccessful) throw HttpError(r.code, "HTTP ${r.code}")
            dest.outputStream().use { o -> r.body!!.byteStream().copyTo(o) }
        }
    }

    fun status() = exec(req("/api/status").get().build())
    fun pull(since: Long, limit: Int = 2000) = exec(req("/api/sync/pull?since=$since&limit=$limit").get().build())
    fun push(data: JSONObject) = exec(req("/api/sync/push").post(data.toString().toRequestBody(json)).build())
    fun timeline(day: String) = exec(req("/api/days/$day/timeline").get().build())
    fun ask(q: String) = exec(req("/api/ask").post(JSONObject().put("q", q).toString().toRequestBody(json)).build())
    fun merge(src: Long, into: Long) =
        exec(req("/api/persons/$src/merge").post(JSONObject().put("into", into).toString().toRequestBody(json)).build())
}
