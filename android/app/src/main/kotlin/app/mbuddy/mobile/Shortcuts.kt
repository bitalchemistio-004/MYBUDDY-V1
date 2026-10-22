package app.mbuddy.mobile

import android.content.Context
import android.content.Intent
import android.net.Uri

/**
 * The first move of a task, done without a model when it is obvious: a search goes straight to its search page by link ("search Maps for a
 * pharmacy" opens Maps on the results), and a task that names an app starts in that app. A small model on the phone loses its way on the
 * launcher; starting it in the right place saves those steps and those mistakes.
 */
object Shortcuts {
    class Pre(val did: String, val result: String)

    private val SEARCH = Regex("(?:search|look up|find|look for)\\s+(?:for\\s+)?(.+?)(?:\\s*,|\\s+then\\b|\\s+and\\s+(?:tell|show|read|give|say|open|play)\\b|\\s+do not\\b|\\s+don't\\b|\\s+in\\s+(?:chrome|maps|google maps|youtube music|youtube|the play store|play store)\\b|\\.?$)")

    private val PLAY = Regex("\\bplay\\s+(?:me\\s+)?(.+?)(?:\\s+(?:on|in|from)\\s+(?:youtube music|youtube|yt music|spotify)\\b|\\s*,|\\s+the first\\b|\\s+then\\b|\\s+and\\b|\\.?$)")
    private val MEDIA = Regex("youtube|yt music|spotify")

    /** The link for a search the goal asks for ("search YouTube for X", or "play X" in a music or video app), and where it opens; null when it is not one. */
    fun searchLink(goal: String): Pair<String, String>? {
        val g = goal.lowercase()
        val raw = SEARCH.find(g)?.groupValues?.get(1) ?: PLAY.find(g)?.takeIf { MEDIA.containsMatchIn(g) }?.groupValues?.get(1) ?: return null
        val q = raw.trim().removePrefix("a ").removePrefix("an ").removePrefix("the ").trim()
        if (q.length < 2) return null
        val e = Uri.encode(q)
        return when {
            g.contains("youtube music") || g.contains("yt music") -> "https://music.youtube.com/search?q=$e" to "YouTube Music"
            g.contains("youtube") -> "https://www.youtube.com/results?search_query=$e" to "YouTube"
            g.contains("spotify") -> "https://open.spotify.com/search/$e" to "Spotify"
            g.contains("play store") || g.contains("google play") -> "https://play.google.com/store/search?q=$e&c=apps" to "the Play Store"
            g.contains("maps") || g.contains(" map ") || g.contains("near me") || g.contains("nearest") -> "geo:0,0?q=$e" to "Maps"
            g.contains("chrome") || g.contains("google") || g.contains("the web") || g.contains("internet") || g.contains("online") -> "https://www.google.com/search?q=$e" to "a web search"
            else -> null
        }
    }

    /** The installed app the goal names ("Open the Clock app", "In Calculator ..."), or null. The longest name that fits wins. */
    fun appInGoal(ctx: Context, goal: String): Pair<String, String>? {
        val words = goal.lowercase().replace(Regex("[^a-z0-9' ]"), " ").split(' ').filter { it.isNotBlank() }
        val skip = setOf("open", "the", "app", "in", "on", "and", "then", "go", "to", "me", "tell", "my", "a", "an", "of", "for", "it", "what", "show")
        val squash = { s: String -> s.lowercase().filter { it.isLetterOrDigit() } }
        for (len in 3 downTo 1) for (i in 0..words.size - len) {
            val window = words.subList(i, i + len)
            if (window.first() in skip || window.last() in skip) continue
            val said = squash(window.joinToString(""))
            // the app's whole name, or a longer name said for it ("Google Maps" for Maps); a word that only starts a name does not count
            Actions.appMatch(ctx, window.joinToString(" "), fuzzy = false)?.takeIf { (_, label) -> said == squash(label) || said.endsWith(squash(label)) }?.let { return it }
        }
        return null
    }

    /** Do the obvious first move for [goal], if there is one. Returns what was done (for the planner's list of steps), or null. */
    fun prepare(ctx: Context, svc: ControlService, goal: String): Pre? {
        searchLink(goal)?.let { (url, where) ->
            val ok = runCatching { svc.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url)).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)); true }.getOrDefault(false)
            if (ok) return Pre("opened $where on the search for it", "the search results are open")
        }
        appInGoal(ctx, goal)?.let { (pkg, label) ->
            if (Risk.appDenied(pkg) != null) return null                                 // the loop refuses it with its reason
            val current = runCatching { svc.root()?.packageName?.toString() }.getOrNull()
            if (current == pkg) return Pre("$label is already open", "it is on the screen")
            val launch = ctx.packageManager.getLaunchIntentForPackage(pkg) ?: return null
            val ok = runCatching { svc.startActivity(launch.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)); true }.getOrDefault(false)
            if (ok) return Pre("opened $label", "it is open")
        }
        return null
    }
}
