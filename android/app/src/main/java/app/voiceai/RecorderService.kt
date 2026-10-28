package app.voiceai

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.media.MediaRecorder
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.os.PowerManager
import android.util.Log
import androidx.core.app.NotificationCompat
import androidx.core.app.ServiceCompat
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * Registra in blocchi da N minuti (Ogg/Opus: un file troncato resta leggibile se il processo muore).
 * Blocco finito -> cartella pending/ -> UploadWorker lo invia, verifica lo SHA-256 e lo cancella.
 * Nome file: <sessione>_<indice>_<inizioMs>.ogg
 */
class RecorderService : Service() {
    companion object {
        const val ACTION_STOP = "stop"
        private const val CHANNEL = "rec"
        private const val NOTIF_ID = 1

        @Volatile var running = false
            private set
        @Volatile var startedAtMs = 0L
            private set
        @Volatile private var current: MediaRecorder? = null

        fun amplitude(): Int = try { current?.maxAmplitude ?: 0 } catch (e: Exception) { 0 }

        fun pendingDir(ctx: Context) = File(ctx.filesDir, "pending").apply { mkdirs() }
        fun recordingDir(ctx: Context) = File(ctx.filesDir, "recording").apply { mkdirs() }

        /** Blocchi rimasti a metà da un crash: si inviano lo stesso. */
        fun recoverOrphans(ctx: Context) {
            if (running) return
            recordingDir(ctx).listFiles()?.forEach { f ->
                if (f.length() > 1024) f.renameTo(File(pendingDir(ctx), f.name)) else f.delete()
            }
        }
    }

    private val handler = Handler(Looper.getMainLooper())
    private var rec: MediaRecorder? = null
    private var file: File? = null
    private var session = ""
    private var idx = 0
    private var wake: PowerManager.WakeLock? = null
    private val rotate = Runnable { finishChunk(); idx++; startChunk() }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_STOP) {
            stopAll()
            return START_NOT_STICKY
        }
        if (running) return START_NOT_STICKY
        recoverOrphans(this)
        ServiceCompat.startForeground(this, NOTIF_ID, notification(), ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE)
        session = SimpleDateFormat("yyyyMMdd-HHmmss", Locale.US).format(Date())
        idx = 0
        wake = (getSystemService(POWER_SERVICE) as PowerManager)
            .newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "voiceai:rec").apply { acquire(24 * 3600 * 1000L) }
        running = true
        startedAtMs = System.currentTimeMillis()
        startChunk()
        return START_NOT_STICKY
    }

    private fun startChunk() {
        val start = System.currentTimeMillis()
        val f = File(recordingDir(this), "${session}_${idx}_$start.ogg")
        val r = MediaRecorder(this)
        try {
            val mic = Mic.select(this, Prefs(this).mic)
            r.setAudioSource(mic.source)
            mic.input?.let { r.setPreferredDevice(it) }
            r.setOutputFormat(MediaRecorder.OutputFormat.OGG)
            r.setAudioEncoder(MediaRecorder.AudioEncoder.OPUS)
            r.setAudioChannels(1)
            r.setAudioSamplingRate(mic.rate)
            r.setAudioEncodingBitRate(32000)   // voce: ~14 MB/ora
            r.setOutputFile(f.absolutePath)
            r.prepare()
            r.start()
        } catch (e: Exception) {
            Log.e("voiceai", "avvio registrazione fallito", e)
            r.release()
            stopAll()
            return
        }
        rec = r; current = r; file = f
        handler.postDelayed(rotate, Prefs(this).chunkMinutes * 60_000L)
    }

    private fun finishChunk() {
        handler.removeCallbacks(rotate)
        val r = rec ?: return
        val f = file
        current = null; rec = null; file = null
        var ok = true
        try { r.stop() } catch (e: RuntimeException) { ok = false }   // stop() subito dopo start(): file vuoto
        r.release()
        if (f != null) {
            if (ok && f.length() > 0) f.renameTo(File(pendingDir(this), f.name)) else f.delete()
        }
        UploadWorker.enqueue(this)
    }

    private fun stopAll() {
        finishChunk()
        Mic.release(this)
        running = false
        wake?.let { if (it.isHeld) it.release() }
        wake = null
        ServiceCompat.stopForeground(this, ServiceCompat.STOP_FOREGROUND_REMOVE)
        stopSelf()
    }

    override fun onDestroy() {
        if (running) stopAll()
        super.onDestroy()
    }

    private fun notification(): Notification {
        val nm = getSystemService(NotificationManager::class.java)!!
        nm.createNotificationChannel(NotificationChannel(CHANNEL, "Registrazione", NotificationManager.IMPORTANCE_LOW))
        val open = PendingIntent.getActivity(this, 0, Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        val stop = PendingIntent.getService(this, 1, Intent(this, RecorderService::class.java).setAction(ACTION_STOP),
            PendingIntent.FLAG_IMMUTABLE)
        return NotificationCompat.Builder(this, CHANNEL)
            .setSmallIcon(R.drawable.ic_mic)
            .setContentTitle("VoiceAi sta registrando")
            .setContentText("Blocchi da ${Prefs(this).chunkMinutes} min inviati al PC")
            .setOngoing(true)
            .setContentIntent(open)
            .addAction(0, "Stop", stop)
            .build()
    }
}

/** Scelta del microfono: quello del telefono, uno Bluetooth (cuffie, custodia) o uno USB-C con il filo. */
object Mic {
    class Choice(val source: Int, val input: android.media.AudioDeviceInfo?, val rate: Int)

    private val BT = setOf(android.media.AudioDeviceInfo.TYPE_BLUETOOTH_SCO, android.media.AudioDeviceInfo.TYPE_BLE_HEADSET)
    private val USB = setOf(android.media.AudioDeviceInfo.TYPE_USB_DEVICE, android.media.AudioDeviceInfo.TYPE_USB_HEADSET,
        android.media.AudioDeviceInfo.TYPE_USB_ACCESSORY)

    fun available(ctx: Context): Set<String> {
        val am = ctx.getSystemService(android.media.AudioManager::class.java)
        val types = am.getDevices(android.media.AudioManager.GET_DEVICES_INPUTS).map { it.type }
        return buildSet {
            add("telefono")
            if (types.any { it in BT } || am.availableCommunicationDevices.any { it.type in BT }) add("bluetooth")
            if (types.any { it in USB }) add("usb")
        }
    }

    fun select(ctx: Context, want: String): Choice {
        val am = ctx.getSystemService(android.media.AudioManager::class.java)
        val inputs = am.getDevices(android.media.AudioManager.GET_DEVICES_INPUTS)
        when (want) {
            "usb" -> inputs.firstOrNull { it.type in USB }?.let {
                return Choice(MediaRecorder.AudioSource.MIC, it, 48000)
            }
            "bluetooth" -> am.availableCommunicationDevices.firstOrNull { it.type in BT }?.let { out ->
                // il microfono Bluetooth si apre come in una chiamata (profilo voce, 16 kHz)
                am.mode = android.media.AudioManager.MODE_IN_COMMUNICATION
                if (am.setCommunicationDevice(out)) {
                    val inp = inputs.firstOrNull { it.type == out.type }
                    return Choice(MediaRecorder.AudioSource.VOICE_COMMUNICATION, inp, 16000)
                }
                am.mode = android.media.AudioManager.MODE_NORMAL
            }
        }
        if (want != "telefono") Log.w("voiceai", "microfono $want non disponibile: uso quello del telefono")
        return Choice(MediaRecorder.AudioSource.MIC, null, 48000)
    }

    fun release(ctx: Context) {
        val am = ctx.getSystemService(android.media.AudioManager::class.java)
        if (am.mode == android.media.AudioManager.MODE_IN_COMMUNICATION) {
            am.clearCommunicationDevice()
            am.mode = android.media.AudioManager.MODE_NORMAL
        }
    }
}
