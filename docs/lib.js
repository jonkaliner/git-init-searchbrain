// Pure helper functions of the Searchbrain website (no DOM, no network), so they can be tested on their own.
// tests/web/lib.test.html runs the tests: open it in a browser through a local web server.

export const STOPWORDS = new Set([
  "a", "an", "the", "and", "or", "of", "in", "on", "for", "with", "from", "to", "at", "by", "is", "are", "me", "my",
  "find", "show", "give", "list", "build", "make", "create", "search", "looking", "look", "which", "what", "who", "where",
  "deal", "flow", "dealflow", "startup", "company", "companies", "that", "this", "please", "some", "any", "all",
  "early", "stage", "seed", "new", "young", "best", "top",
]);

// Regions people type in a request, mapped to the countries in the data
export const REGIONS = {
  europe: ["Austria", "Belgium", "Bulgaria", "Croatia", "Cyprus", "Czech Republic", "Czechia", "Denmark", "Estonia", "Finland", "France",
    "Germany", "Greece", "Hungary", "Iceland", "Ireland", "Italy", "Latvia", "Lithuania", "Luxembourg", "Malta", "Netherlands", "Norway",
    "Poland", "Portugal", "Romania", "Serbia", "Slovakia", "Slovenia", "Spain", "Sweden", "Switzerland", "Ukraine", "United Kingdom"],
};
export const COUNTRY_ALIASES = { us: "United States", usa: "United States", america: "United States", uk: "United Kingdom", britain: "United Kingdom" };

export function stem(word) {
  return word.length > 3 && word.endsWith("s") && !word.endsWith("ss") ? word.slice(0, -1) : word;
}

export function tokenize(text) {
  return (String(text || "").toLowerCase().match(/[a-z0-9]+/g) || []).map(stem);
}

/** The topic words of a request: no filler words like "build a deal flow for". */
export function topicWords(text) {
  const words = tokenize(text).filter((w) => !STOPWORDS.has(w) && !STOPWORDS.has(w + "s") && w.length > 1);
  return [...new Set(words)];
}

/** Countries named in a request ("Germany", "Europe", "US"), as a set of country names. Empty = no country asked. */
export function countriesInText(text, knownCountries) {
  const lower = String(text || "").toLowerCase();
  const found = new Set();
  for (const country of knownCountries) {
    if (new RegExp(`\\b${country.toLowerCase().replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\b`).test(lower)) found.add(country);
  }
  for (const [alias, country] of Object.entries(COUNTRY_ALIASES)) {
    // "us" and "uk" are only a country when written in capitals; "show us robot hands" is not a US filter
    const asked = alias.length <= 3 ? new RegExp(`\\b${alias.toUpperCase()}\\b`).test(String(text || "")) : new RegExp(`\\b${alias}\\b`).test(lower);
    if (asked && knownCountries.includes(country)) found.add(country);
  }
  for (const [region, countries] of Object.entries(REGIONS)) {
    if (new RegExp(`\\b${region}\\b`).test(lower)) countries.filter((c) => knownCountries.includes(c)).forEach((c) => found.add(c));
  }
  return found;
}

/** The words that count as topic words once the countries are taken out ("Germany" is a filter, not a topic). */
export function topicWordsWithoutCountries(text, countries) {
  const countryWords = new Set([...countries].flatMap((c) => tokenize(c)));
  const aliasWords = new Set([...Object.keys(COUNTRY_ALIASES), ...Object.keys(REGIONS)]);
  return topicWords(text).filter((w) => !countryWords.has(w) && !aliasWords.has(w));
}

/**
 * Rank the startups for a request like the database search does: a startup needs at least half of the
 * words; a word in the name or the one-liner counts double compared to the industry or the long summary.
 */
export function searchStartups(startups, query, { limit = 12 } = {}) {
  const knownCountries = [...new Set(startups.map((s) => s.country).filter(Boolean))];
  const countries = countriesInText(query, knownCountries);
  const words = topicWordsWithoutCountries(query, countries);
  if (!words.length) return [];
  const need = Math.ceil(words.length / 2);
  // a short word like "ai" must be a whole word; a longer one also matches the start of a word ("robot" finds "robotic")
  const hasWord = (tokens, w) => (w.length <= 3 ? tokens.includes(w) : tokens.some((t) => t.startsWith(w)));
  const ranked = [];
  for (const s of startups) {
    if (countries.size && !countries.has(s.country)) continue;
    const headline = tokenize(`${s.name} ${s.description || ""}`);
    const background = tokenize(`${s.industry || ""} ${s.summary || ""} ${s.topic || ""}`);
    let score = 0;
    let matched = 0;
    for (const w of words) {
      const inHeadline = hasWord(headline, w);
      const inBackground = hasWord(background, w);
      score += (inHeadline ? 2 : 0) + (inBackground ? 1 : 0);
      matched += inHeadline || inBackground ? 1 : 0;
    }
    if (matched >= need) ranked.push({ s, score });
  }
  ranked.sort((a, b) => b.score - a.score || a.s.name.localeCompare(b.s.name));
  return ranked.slice(0, limit).map((item) => item.s);
}

/** True for messages like "Build a deal flow for ...": they can be turned into a Scout. */
export function isDealFlowRequest(text) {
  return /\bdeal\s*-?\s*flow\b/i.test(text) || /^\s*(build|make|create)\b.*\bscout\b/i.test(text);
}

export function safeUrl(url) {
  if (!url) return null;
  try {
    const parsed = new URL(/^https?:\/\//i.test(url) ? url : `https://${url}`);
    return ["http:", "https:"].includes(parsed.protocol) && parsed.hostname.includes(".") ? parsed.href : null;
  } catch {
    return null;
  }
}

export function placeOf(s) {
  return [s.city, s.country].filter(Boolean).join(", ") || "location unknown";
}

export function stars(score) {
  const n = Math.max(0, Math.min(5, Number(score) || 0));
  return "★".repeat(n) + "☆".repeat(5 - n);
}

export function greeting(date, name) {
  const hour = date.getHours();
  const text = hour < 12 ? "Good morning" : hour < 18 ? "Good afternoon" : "Good evening";
  return name ? `${text}, ${name}` : text;
}

/** A short title for a Scout card: the request without "Build a deal flow for". */
export function scoutTitle(request, max = 60) {
  const text = String(request || "").replace(/^\s*(please\s+)?(build|make|create|find)\s+(me\s+)?(a\s+|the\s+)?(deal\s*flow\s*)?(for|of|on)?\s*/i, "").trim();
  const title = text.charAt(0).toUpperCase() + text.slice(1);
  return title.length > max ? `${title.slice(0, max - 1).trimEnd()}…` : title || "Scout";
}

/** Matches of a Scout that appeared after the last visit (or in the last 7 days if there was no visit yet). */
export function newMatches(scout, lastVisit, today = new Date()) {
  const since = lastVisit || new Date(today.getTime() - 7 * 86400000).toISOString().slice(0, 10);
  return scout.matches.filter((m) => m.firstSeen > since);
}

export function matchesFilters(s, f) {
  if (f.keyword) {
    const text = `${s.name} ${s.description || ""} ${s.summary || ""} ${s.industry || ""} ${s.country || ""} ${s.city || ""}`.toLowerCase();
    if (!f.keyword.toLowerCase().split(/\s+/).filter(Boolean).every((w) => text.includes(w))) return false;
  }
  if (f.countries?.length && !f.countries.includes(s.country)) return false;
  if (f.since && !(s.year >= f.since)) return false;
  if (f.located && !s.lat) return false;
  if (f.yc && !s.batch) return false;
  return true;
}

export function countBy(items, keyOf, limit = 12) {
  const counts = new Map();
  for (const item of items) {
    const key = keyOf(item) ?? "unknown";
    counts.set(key, (counts.get(key) || 0) + 1);
  }
  return [...counts.entries()].sort((a, b) => b[1] - a[1] || String(a[0]).localeCompare(String(b[0]))).slice(0, limit);
}

/** CSV text. Cells that start with = + - @ get a leading quote, so a spreadsheet does not run them as formulas. */
export function toCsv(rows, columns) {
  const cell = (value) => {
    let text = value == null ? "" : String(value);
    if (/^[=+\-@\t\r]/.test(text)) text = `'${text}`;
    return /[",\n\r]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
  };
  return [columns.map((c) => cell(c.label)).join(","), ...rows.map((row) => columns.map((c) => cell(c.value(row))).join(","))].join("\r\n");
}

/** "Mimic Robotics, Zurich" style compact record that is sent to the chat Worker. */
export function workerRecord(s) {
  const pick = { name: s.name, what: s.description, industry: s.industry, country: s.country, city: s.city, founded: s.year, website: s.link, yc_batch: s.batch, programs: s.programs };
  return Object.fromEntries(Object.entries(pick).filter(([, v]) => v !== undefined && v !== null && v !== ""));
}
