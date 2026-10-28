package app.voiceai

import android.content.Context
import android.util.Log
import androidx.work.BackoffPolicy
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.util.concurrent.TimeUnit

private val NET = Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build()

/** Invia i blocchi in pending/. Il file locale si cancella SOLO se il server conferma lo stesso SHA-256. */
class UploadWorker(ctx: Context, params: WorkerParameters) : CoroutineWorker(ctx, params) {
    companion object {
        fun enqueue(ctx: Context) {
            val w = OneTimeWorkRequestBuilder<UploadWorker>().setConstraints(NET)
                .setBackoffCriteria(BackoffPolicy.EXPONENTIAL, 30, TimeUnit.SECONDS).build()
            WorkManager.getInstance(ctx).enqueueUniqueWork("upload", ExistingWorkPolicy.APPEND_OR_REPLACE, w)
        }

        fun pendingCount(ctx: Context) = RecorderService.pendingDir(ctx).listFiles()?.size ?: 0
    }

    override suspend fun doWork(): Result = withContext(Dispatchers.IO) {
        if (!Prefs(applicationContext).configured) return@withContext Result.success()
        val api = Api(applicationContext)
        var failed = false
        val files = RecorderService.pendingDir(applicationContext).listFiles()?.sortedBy { it.name } ?: emptyList()
        for (f in files) {
            val parts = f.nameWithoutExtension.split("_")
            if (parts.size != 3) { f.delete(); continue }
            try {
                val sha = Api.sha256(f)
                val r = api.upload(f, sha, parts[0], parts[1].toInt(), parts[2].toLong() / 1000.0)
                if (r.optBoolean("ok") && r.optString("sha256").equals(sha, ignoreCase = true)) {
                    f.delete()
                } else {
                    Log.w("voiceai", "upload ${f.name} non verificato: $r")
                    failed = true
                }
            } catch (e: Exception) {
                Log.w("voiceai", "upload ${f.name}: ${e.message}")
                failed = true
                break   // server irraggiungibile: inutile provare gli altri ora
            }
        }
        SyncWorker.now(applicationContext)
        if (failed) Result.retry() else Result.success()
    }
}

/** Sync bidirezionale con il PC: prima invio le mie modifiche, poi scarico le novità. */
class SyncWorker(ctx: Context, params: WorkerParameters) : CoroutineWorker(ctx, params) {
    companion object {
        fun now(ctx: Context) {
            val w = OneTimeWorkRequestBuilder<SyncWorker>().setConstraints(NET)
                .setBackoffCriteria(BackoffPolicy.EXPONENTIAL, 30, TimeUnit.SECONDS).build()
            WorkManager.getInstance(ctx).enqueueUniqueWork("sync", ExistingWorkPolicy.APPEND_OR_REPLACE, w)
        }

        fun schedulePeriodic(ctx: Context) {
            val w = PeriodicWorkRequestBuilder<SyncWorker>(15, TimeUnit.MINUTES).setConstraints(NET).build()
            WorkManager.getInstance(ctx).enqueueUniquePeriodicWork("sync-periodic", ExistingPeriodicWorkPolicy.KEEP, w)
        }
    }

    override suspend fun doWork(): Result = withContext(Dispatchers.IO) {
        val prefs = Prefs(applicationContext)
        if (!prefs.configured) return@withContext Result.success()
        try {
            Sync(applicationContext).run()
            MemoReceiver.scheduleAll(applicationContext)
            Result.success()
        } catch (e: Exception) {
            Log.w("voiceai", "sync: ${e.message}")
            Result.retry()
        }
    }
}
