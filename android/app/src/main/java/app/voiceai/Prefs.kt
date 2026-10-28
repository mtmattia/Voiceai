package app.voiceai

import android.content.Context

/** Impostazioni locali (SharedPreferences). */
class Prefs(ctx: Context) {
    private val p = ctx.getSharedPreferences("voiceai", Context.MODE_PRIVATE)

    var server: String
        get() = p.getString("server", "") ?: ""
        set(v) = p.edit().putString("server", v.trim().trimEnd('/')).apply()
    var token: String
        get() = p.getString("token", "") ?: ""
        set(v) = p.edit().putString("token", v.trim()).apply()
    var device: String
        get() = p.getString("device", "telefono") ?: "telefono"
        set(v) = p.edit().putString("device", v.trim().ifEmpty { "telefono" }).apply()
    var chunkMinutes: Int
        get() = p.getInt("chunk_minutes", 5)
        set(v) = p.edit().putInt("chunk_minutes", v.coerceIn(1, 30)).apply()
    var keepScreenOn: Boolean
        get() = p.getBoolean("keep_screen_on", true)
        set(v) = p.edit().putBoolean("keep_screen_on", v).apply()
    var blackAfterSec: Int
        get() = p.getInt("black_after_sec", 8)
        set(v) = p.edit().putInt("black_after_sec", v.coerceIn(3, 120)).apply()
    /** telefono | bluetooth | usb */
    var mic: String
        get() = p.getString("mic", "telefono") ?: "telefono"
        set(v) = p.edit().putString("mic", v).apply()
    var lastRev: Long
        get() = p.getLong("last_rev", 0)
        set(v) = p.edit().putLong("last_rev", v).apply()
    var lastSync: Long
        get() = p.getLong("last_sync", 0)
        set(v) = p.edit().putLong("last_sync", v).apply()

    val configured get() = server.isNotEmpty() && token.isNotEmpty()
}
