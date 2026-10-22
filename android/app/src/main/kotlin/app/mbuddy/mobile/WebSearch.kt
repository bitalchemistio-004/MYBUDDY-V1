package app.mbuddy.mobile

import android.content.Context
import android.net.Uri
import android.text.Html
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit

/**
 * Questions about the world are answered from the web, not from the model's memory (a small model's memory is old and often wrong):
 * DuckDuckGo's results (the same provider as the PC app, utils/search_provider.py), the top page read for more detail, and the brain
 * answers in a sentence or two from what was found. Wikipedia is the fallback when DuckDuckGo refuses.
 */
object WebSearch {
    class Hit(val title: String, val url: String, val snippet: String)
    class Answer(val say: String, val sources: List<Hit>)

    private const val UA = "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Mobile Safari/537.36"
    private val pool = Executors.newFixedThreadPool(2)

    private fun get(url: String, timeoutMs: Int = 6000): String? = runCatching {
        val c = URL(url).openConnection() as HttpURLConnection
        c.connectTimeout = timeoutMs; c.readTimeout = timeoutMs; c.instanceFollowRedirects = true
        c.setRequestProperty("User-Agent", UA); c.setRequestProperty("Accept-Language", "en")
        try { if (c.responseCode !in 200..299) null else c.inputStream.bufferedReader().use { it.readText().take(400_000) } } finally { c.disconnect() }
    }.getOrNull()

    private fun text(html: String): String = Html.fromHtml(html, Html.FROM_HTML_MODE_LEGACY).toString().replace(Regex("\\s+"), " ").trim()

    /** Up to [max] results for [query]; empty when the web cannot be reached. */
    fun search(query: String, max: Int = 5): List<Hit> {
        val page = get("https://html.duckduckgo.com/html/?q=" + Uri.encode(query))
        val hits = ArrayList<Hit>()
        if (page != null && !page.contains("anomaly-modal")) {
            val link = Regex("class=\"result__a\" href=\"([^\"]+)\"[^>]*>(.*?)</a>", RegexOption.DOT_MATCHES_ALL)
            val snip = Regex("class=\"result__snippet\"[^>]*>(.*?)</a>", RegexOption.DOT_MATCHES_ALL)
            val links = link.findAll(page).toList(); val snips = snip.findAll(page).toList()
            for ((i, m) in links.withIndex()) {
                if (hits.size >= max) break
                val href = m.groupValues[1].replace("&amp;", "&")
                val url = Uri.parse(if (href.startsWith("//")) "https:$href" else href).getQueryParameter("uddg") ?: href
                if (url.contains("duckduckgo.com/y.js")) continue                         // an advert
                hits.add(Hit(text(m.groupValues[2]), url, snips.getOrNull(i)?.groupValues?.get(1)?.let { text(it) } ?: ""))
            }
        }
        if (hits.isEmpty()) hits.addAll(wikipedia(query, max))
        return hits
    }

    private fun wikipedia(query: String, max: Int): List<Hit> {
        val body = get("https://en.wikipedia.org/w/rest.php/v1/search/page?limit=$max&q=" + Uri.encode(query)) ?: return emptyList()
        val pages = runCatching { JSONObject(body).getJSONArray("pages") }.getOrNull() ?: return emptyList()
        return (0 until pages.length()).map { i ->
            val p = pages.getJSONObject(i)
            Hit(p.optString("title"), "https://en.wikipedia.org/wiki/" + Uri.encode(p.optString("key")), text(p.optString("excerpt")) + " " + p.optString("description"))
        }
    }

    /** The readable start of a page: its paragraphs, at most [max] characters; "" when it cannot be read in time. */
    fun excerpt(url: String, max: Int = 1200): String {
        val page = get(url, 3500) ?: return ""
        val body = page.replace(Regex("(?is)<(script|style|noscript|nav|header|footer)[^>]*>.*?</\\1>"), " ")
        val paras = Regex("(?is)<p[^>]*>(.*?)</p>").findAll(body).map { text(it.groupValues[1]) }.filter { it.length > 40 }
        return paras.joinToString(" ").take(max)
    }

    private const val ANSWER_SYSTEM = """You answer the owner's question from web search results, for a voice assistant that reads your answer aloud.
Answer in one or two short, plain sentences: the answer first, with the numbers, names or dates the results give. No lists, no links, no emoji.
Use only the results. If they do not answer the question, say you could not find it. Today is {{NOW}}."""

    /** Search, read the best pages, and answer [question] in a sentence or two. Never throws; null when the web cannot be reached. */
    fun answer(ctx: Context, question: String, query: String = question, now: String = ""): Answer? {
        val t0 = System.currentTimeMillis()
        val hits = search(query)
        if (hits.isEmpty()) return null
        // the top two pages are read at the same time, for detail a snippet leaves out
        val reads = hits.take(2).map { h -> pool.submit<String> { excerpt(h.url, 900) } }
        val pages = reads.map { runCatching { it.get(4, TimeUnit.SECONDS) }.getOrDefault("") }
        val found = hits.mapIndexed { i, h -> "${i + 1}. ${h.title}\n${h.snippet}" + (pages.getOrNull(i)?.takeIf { it.isNotBlank() }?.let { "\nPage: $it" } ?: "") }.joinToString("\n\n")
        android.util.Log.i("MBuddyWeb", "searched '$query' in ${System.currentTimeMillis() - t0} ms: ${hits.size} results")
        val user = "Question: $question\n\nSearch results:\n$found\n\nAnswer:"
        val say = NativeBrain.plainAnswer(ctx, ANSWER_SYSTEM.replace("{{NOW}}", now), user)
            ?: hits.first().snippet.ifBlank { hits.first().title }                         // no brain answered: read the best result itself
        android.util.Log.i("MBuddyWeb", "answered in ${System.currentTimeMillis() - t0} ms: ${say.take(200)}")
        return Answer(say.trim(), hits.take(3))
    }
}
