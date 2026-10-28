package app.voiceai

import android.app.AlarmManager
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import androidx.core.app.NotificationCompat

/** Notifica i memo alla scadenza (anche senza rete: legge la copia locale). */
class MemoReceiver : BroadcastReceiver() {
    companion object {
        private const val CHANNEL = "memo"

        private fun pi(ctx: Context, uid: String) = PendingIntent.getBroadcast(
            ctx, uid.hashCode(), Intent(ctx, MemoReceiver::class.java).putExtra("uid", uid),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)

        /** Riprogramma gli avvisi dei memo aperti con scadenza nei prossimi 2 giorni. */
        fun scheduleAll(ctx: Context) {
            val am = ctx.getSystemService(AlarmManager::class.java) ?: return
            val now = System.currentTimeMillis()
            val db = LocalDb.get(ctx)
            for (m in db.openMemos("todo") + db.openMemos("agenda")) {
                val due = ((m["due_ts"] as? Double) ?: continue) * 1000
                val uid = m["uid"] as String
                if (due.toLong() in now..(now + 2 * 86_400_000L)) {
                    // inesatto ma consentito senza permessi speciali: arriva entro pochi minuti
                    am.setAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, due.toLong(), pi(ctx, uid))
                }
            }
        }
    }

    override fun onReceive(ctx: Context, intent: Intent) {
        val uid = intent.getStringExtra("uid") ?: return
        val m = LocalDb.get(ctx).q("SELECT * FROM memos WHERE uid=? AND done=0 AND deleted=0", uid).firstOrNull() ?: return
        val nm = ctx.getSystemService(NotificationManager::class.java) ?: return
        nm.createNotificationChannel(NotificationChannel(CHANNEL, "Memo", NotificationManager.IMPORTANCE_HIGH))
        val agenda = m["kind"] == "agenda"
        val open = PendingIntent.getActivity(ctx, if (agenda) 3 else 2,
            Intent(ctx, MemoActivity::class.java).putExtra("kind", m["kind"] as? String ?: "todo"), PendingIntent.FLAG_IMMUTABLE)
        val guessed = m["due_kind"] == "ipotizzata"
        val descr = (m["descr"] as? String).orEmpty()
        nm.notify(uid.hashCode(), NotificationCompat.Builder(ctx, CHANNEL)
            .setSmallIcon(R.drawable.ic_mic)
            .setContentTitle(m["text"] as String)
            .setContentText(descr.ifEmpty { if (agenda) "Appuntamento adesso" else if (guessed) "Di solito lo fai a quest'ora" else "Scadenza adesso" })
            .setStyle(NotificationCompat.BigTextStyle().bigText(descr.ifEmpty { if (agenda) "Appuntamento adesso" else "Scadenza adesso" }))
            .setContentIntent(open)
            .setAutoCancel(true)
            .build())
    }
}
