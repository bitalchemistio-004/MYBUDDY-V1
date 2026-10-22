package app.mbuddy.mobile

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.provider.ContactsContract
import android.telephony.PhoneNumberUtils
import android.telephony.TelephonyManager
import androidx.core.content.ContextCompat

/** Looks a person up by name in the phone's contacts (only when the owner allowed it), so "message Baba" can open the right chat. */
object Contacts {
    class Match(val name: String, val number: String)

    fun allowed(ctx: Context) = ContextCompat.checkSelfPermission(ctx, Manifest.permission.READ_CONTACTS) == PackageManager.PERMISSION_GRANTED

    private fun squash(t: String) = t.lowercase().filter { it.isLetterOrDigit() }

    private fun distance(a: String, b: String): Int {
        var prev = IntArray(b.length + 1) { it }
        for (i in 1..a.length) {
            val cur = IntArray(b.length + 1); cur[0] = i
            for (j in 1..b.length) cur[j] = minOf(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + if (a[i - 1] == b[j - 1]) 0 else 1)
            prev = cur
        }
        return prev[b.length]
    }

    /** The contacts that [query] could mean, best first: the same name, a name that starts or contains it, or one that sounds near it. */
    fun find(ctx: Context, query: String): List<Match> {
        val want = squash(query)
        if (!allowed(ctx) || want.length < 2) return emptyList()
        val scored = ArrayList<Pair<Int, Match>>()
        ctx.contentResolver.query(ContactsContract.CommonDataKinds.Phone.CONTENT_URI,
            arrayOf(ContactsContract.CommonDataKinds.Phone.DISPLAY_NAME, ContactsContract.CommonDataKinds.Phone.NUMBER), null, null, null)?.use { c ->
            while (c.moveToNext()) {
                val name = c.getString(0) ?: continue; val number = c.getString(1) ?: continue
                val have = squash(name)
                val words = name.lowercase().split(' ', '-').map { squash(it) }.filter { it.isNotEmpty() }
                val score = when {
                    have == want -> 0
                    words.any { it == want } -> 1
                    have.startsWith(want) || words.any { it.startsWith(want) } -> 2
                    have.contains(want) -> 3
                    words.any { want.length >= 3 && distance(it, want) <= maxOf(1, want.length / 4) } -> 4
                    else -> continue
                }
                scored.add(score to Match(name, number))
            }
        }
        return scored.sortedBy { it.first }.map { it.second }.distinctBy { squash(it.name) + digits(it.number) }.take(5)
    }

    /** Every contact with a number (name, number), read once. */
    fun all(ctx: Context): List<Match> {
        if (!allowed(ctx)) return emptyList()
        val out = ArrayList<Match>()
        ctx.contentResolver.query(ContactsContract.CommonDataKinds.Phone.CONTENT_URI,
            arrayOf(ContactsContract.CommonDataKinds.Phone.DISPLAY_NAME, ContactsContract.CommonDataKinds.Phone.NUMBER), null, null, null)?.use { c ->
            while (c.moveToNext()) { val n = c.getString(0) ?: continue; val p = c.getString(1) ?: continue; out.add(Match(n, p)) }
        }
        return out
    }

    /** How well [spoken] fits [name]: 0 the same, 1 one of its words, 2 starts one, 3 close in sound; null when it does not fit. */
    fun fit(name: String, spoken: String): Int? {
        val want = squash(spoken)
        if (want.length < 3) return null
        val have = squash(name)
        val words = name.lowercase().split(' ', '-').map { squash(it) }.filter { it.isNotEmpty() }
        return when {
            have == want -> 0
            words.any { it == want } -> 1
            words.any { it.length >= 3 && it.startsWith(want) } -> 2
            words.any { distance(it, want) <= maxOf(1, want.length / 4) } -> 3
            else -> null
        }
    }

    fun digits(n: String) = n.filter { it.isDigit() }

    /** The number in international form (WhatsApp needs the country code); the phone's own country is assumed for a number saved without one. */
    fun international(ctx: Context, raw: String): String {
        if (raw.trim().startsWith("+")) return digits(raw)
        val tm = ctx.getSystemService(Context.TELEPHONY_SERVICE) as? TelephonyManager
        val iso = (tm?.networkCountryIso?.ifBlank { tm.simCountryIso } ?: "").uppercase()
        val e164 = if (iso.isNotEmpty()) PhoneNumberUtils.formatNumberToE164(raw, iso) else null
        return digits(e164 ?: raw)
    }
}
