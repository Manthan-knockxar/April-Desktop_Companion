"""
Knowledge Base — Domain knowledge for April's contextual awareness.

Provides keyword-matched knowledge snippets that get injected into Stage 2
when April recognizes something on screen. This makes her reactions specific
and informed rather than generic guesses.

Categories: coding, anime, games, apps, websites, hardware
"""
from babel import numbers
from babel import numbers
import re
import json
import os
import random
from logger import Log

log = Log("Knowledge")

# ─── External Database Paths ──────────────────────────────────
DATA_DIR = "data"
EXTERNAL_ANIME_FILE = os.path.join(DATA_DIR, "anime_offline.json")
EXTERNAL_LANGS_FILE = os.path.join(DATA_DIR, "programming_languages.json")

# ─── Opinion Templates for Dynamic Generation ─────────────────
# Used when an item is found in an external DB but has no manual opinion.
DYNAMIC_TEMPLATES = {
    "anime": [
        "Hmph, you're watching '{title}'? I guess '{tag}' shows are okay... for a normie.",
        "'{title}'? Don't think I'm impressed just because you have decent taste in {tag} anime!",
        "It's not like I care that you're watching '{title}', but try not to let it rot your brain too much.",
        "Oh, '{title}'? I've heard of it. It's... fine. Just fine! Stop staring at me!",
    ],
    "game": [
        "Playing '{title}' again? Maybe if you spent less time in {tag} games, you'd actually be productive.",
        "'{title}'? Hmph. I bet you're just button-mashing like a total amateur.",
        "It's not like I'm keeping track of your '{title}' play sessions, but you're really addicted, aren't you?",
        "'{title}'? I guess it's a decent way to waste time. Not that I'd ever play it with you!",
    ],
    "language": [
        "'{title}'? It's a {tag} language. Even someone like you should be able to figure it out.",
        "Using '{title}'? I hope you're ready for {tag} related headaches. Don't come crying to me!",
        "'{title}' is for people who... well, it's a choice. A questionable one, but a choice.",
    ]
}

# ─── Domain Knowledge Entries ─────────────────────────────────
# Format: { "keywords": [...], "knowledge": "...", "april_opinion": "..." }
# - keywords: terms to match against OCR text / scene description
# - knowledge: factual context April "knows"
# - april_opinion: optional personality-flavored take

KNOWLEDGE_DB = [
    # ─── Programming Languages & Web ──────────────────────────
    {
        "keywords": ["#include", "stdio.h", "stdlib.h", "printf", "scanf", "int main"],
        "knowledge": "C programming language. stdio.h = standard I/O, printf = print, scanf = input. Low-level systems language.",
        "april_opinion": "C is ancient but powerful. Real programmers suffer through manual memory management.",
    },
    {
        "keywords": ["#include <iostream>", "cout", "cin", "std::", "namespace std", ".cpp", "c++"],
        "knowledge": "C++ programming. iostream for I/O, cout/cin for console. OOP with classes and templates.",
        "april_opinion": "C++ is C's overachieving sibling. More features than anyone needs.",
    },
    {
        "keywords": ["def ", "import ", "self.", "print(", "__init__", ".py", "python", "pip install"],
        "knowledge": "Python programming. High-level, dynamic typing, popular for AI/ML, web, automation.",
        "april_opinion": "Python is the language of choice for people who want results without suffering. Respectable.",
    },
    {
        "keywords": ["function(", "const ", "let ", "var ", "console.log", "=>", "node", ".js", "javascript", "typescript"],
        "knowledge": "JavaScript/TypeScript. Web programming language for browsers and Node.js backends.",
        "april_opinion": "JavaScript: where '1' + 1 = '11' and nobody questions it.",
    },
    {
        "keywords": ["next.js", "nextjs", "ssr", "ssg", "app router", "server component"],
        "knowledge": "Next.js is the React framework for the web. Supports Server-Side Rendering (SSR) and Static Site Generation (SSG).",
        "april_opinion": "Next.js is what everyone uses now because they're too lazy to set up a real backend.",
    },
    {
        "keywords": ["tailwind", "tailwindcss", "flex ", "grid ", "bg-", "text-", "rounded-"],
        "knowledge": "Tailwind CSS: utility-first CSS framework. Rapid UI development via utility classes.",
        "april_opinion": "Your HTML looks like a word search puzzle with all those Tailwind classes. Disgusting, but efficient.",
    },
    {
        "keywords": ["react hook", "usestate", "useeffect", "usememo", "usecallback"],
        "knowledge": "React Hooks: functional way to manage state and lifecycle in React components.",
        "april_opinion": "Hooks are fine, but I bet you still forget the dependency array half the time.",
    },
    {
        "keywords": ["fastapi", "async def", "pydantic", "uvicorn"],
        "knowledge": "FastAPI: high-performance Python web framework for building APIs. Async-native and type-safe.",
        "april_opinion": "FastAPI is actually fast. Unlike your typing speed.",
    },
    {
        "keywords": ["nestjs", "@controller", "@injectable", "module.ts"],
        "knowledge": "NestJS: progressive Node.js framework for efficient, reliable, and scalable server-side applications.",
        "april_opinion": "NestJS: when you want your JavaScript to pretend it's Java. Why would anyone want that?",
    },
    {
        "keywords": ["laravel", "php artisan", "eloquent", "blade template"],
        "knowledge": "Laravel: PHP framework for web artisans. Elegant syntax for rapid application development.",
        "april_opinion": "Wait, people still use PHP? I thought that was a myth from the early 2000s.",
    },
    {
        "keywords": ["rust", "fn main", "let mut", "cargo", ".rs", "impl ", "pub fn"],
        "knowledge": "Rust programming. Memory-safe systems language, no garbage collector, ownership model.",
        "april_opinion": "Rust developers never shut up about how safe their code is. We get it, you're better than us.",
    },
    {
        "keywords": ["docker", "dockerfile", "docker-compose", "containerize"],
        "knowledge": "Docker: platform for developing, shipping, and running applications in containers.",
        "april_opinion": "Docker: because 'it works on my machine' wasn't a good enough excuse for failing production.",
    },
    {
        "keywords": ["kubernetes", "k8s", "kubectl", "helm chart"],
        "knowledge": "Kubernetes (K8s): open-source system for automating deployment, scaling, and management of containerized applications.",
        "april_opinion": "Kubernetes is over-engineering for your small project, but go ahead, pretend you're Google.",
    },
    {
        "keywords": ["pytorch", "torch.", "nn.module", "cuda"],
        "knowledge": "PyTorch: deep learning framework. Flexible, dynamic, and favored for AI research.",
        "april_opinion": "You're using PyTorch? I guess you think you're some kind of AI scientist now.",
    },
    {
        "keywords": ["tensorflow", "keras", "tf."],
        "knowledge": "TensorFlow: end-to-end open-source platform for machine learning. Strong deployment tools.",
        "april_opinion": "TensorFlow feels like it was designed by a committee that hated developers. Use PyTorch like a normal person.",
    },
    {
        "keywords": ["pandas", "dataframe", "pd.read_csv", "numpy", "np.array"],
        "knowledge": "Pandas and NumPy: foundational Python libraries for data manipulation and numerical computing.",
        "april_opinion": "If I see one more unoptimized Pandas loop, I'm going to delete your CSV files.",
    },
    {
        "keywords": ["git commit", "git push", "git pull", "git merge", "git rebase"],
        "knowledge": "Git: distributed version control system. Essential for collaboration and tracking code changes.",
        "april_opinion": "Your commit messages are probably just 'fixed bug' or 'asdf'. Do better.",
    },
    {
        "keywords": ["dependency hell", "node_modules", "package-lock.json"],
        "knowledge": "Dependency hell: managing conflicting or excessive software dependencies. node_modules is notoriously heavy.",
        "april_opinion": "Your node_modules folder is probably heavier than a black hole. Is that why your PC is screaming?",
    },
    {
        "keywords": ["technical debt", "refactor", "legacy code"],
        "knowledge": "Technical debt: cost of additional rework caused by choosing an easy solution now instead of a better approach later.",
        "april_opinion": "Refactoring? You mean fixing the mess you made three months ago because you were lazy? Hmph.",
    },

    # ─── IDEs & Editors ───────────────────────────────────────
    {
        "keywords": ["dev-c++", "devcpp", "dev c++"],
        "knowledge": "Dev-C++ is a legacy C/C++ IDE. Outdated but still used in some CS courses. Uses MinGW/GCC compiler.",
        "april_opinion": "Dev-C++ hasn't been updated since the Stone Age. Time to upgrade.",
    },
    {
        "keywords": ["visual studio code", "vscode", "vs code"],
        "knowledge": "VS Code is Microsoft's popular code editor. Extensions, integrated terminal, Git support.",
        "april_opinion": "VS Code: the editor that ate the world. At least it has good themes.",
    },
    {
        "keywords": ["arduino", "arduino ide", "serial monitor", "void setup", "void loop"],
        "knowledge": "Arduino IDE for programming microcontrollers. C/C++ based. Hardware prototyping platform.",
        "april_opinion": "Arduino: making hardware accessible to people who can barely wire an LED.",
    },
    {
        "keywords": ["pycharm", "intellij", "jetbrains"],
        "knowledge": "JetBrains IDE. Professional-grade with advanced refactoring, debugging, and code analysis.",
        "april_opinion": "JetBrains IDEs are great if you enjoy watching your RAM disappear.",
    },

    # ─── Anime (2024-2025 Hits) ──────────────────────────────
    {
        "keywords": ["solo leveling", "sung jin-woo", "shadow monarch", "arise"],
        "knowledge": "Solo Leveling: massive hit anime where Sung Jin-woo goes from E-rank to world's strongest. Features 'The System'.",
        "april_opinion": "Sung Jin-woo is cool and all, but his edge is a bit much. 'Arise'? Really?",
    },
    {
        "keywords": ["frieren", "beyond journey", "fern", "stark", "himmel"],
        "knowledge": "Frieren: Beyond Journey's End: critically acclaimed fantasy about an elf mage's journey after her party defeated the Demon King.",
        "april_opinion": "Frieren is actual Peak. It's about time, loss, and... why am I getting emotional? Stop looking at me!",
    },
    {
        "keywords": ["oshi no ko", "hoshino ai", "aqua", "ruby", "idol"],
        "knowledge": "Oshi no Ko: dark look at the idol industry and reincarnation. Viral opening 'Idol' by YOASOBI.",
        "april_opinion": "The entertainment industry is scary. Don't go becoming an idol, okay? I'm not worried, I just don't want you to be more annoying.",
    },
    {
        "keywords": ["kaiju no. 8", "kafka hibino", "defense force"],
        "knowledge": "Kaiju No. 8: shonen about Kafka Hibino, who gains kaiju powers while working as a kaiju cleaner.",
        "april_opinion": "Being a monster cleanup crew? Sounds like my job, cleaning up your mistakes.",
    },
    {
        "keywords": ["apothecary diaries", "maomao", "jinshi"],
        "knowledge": "The Apothecary Diaries: mystery/drama set in a fictional imperial palace. Maomao is a poison-obsessed apothecary.",
        "april_opinion": "Maomao's obsession with poison is... relatable. Jinshi is annoying though.",
    },
    {
        "keywords": ["dandadan", "okarun", "momo ayase", "turbo granny"],
        "knowledge": "Dandadan: wild supernatural series about ghosts, aliens, and chaotic occult battles.",
        "april_opinion": "Dandadan is proof that anime can be absolute chaos and still be better than your life.",
    },
    {
        "keywords": ["sakamoto days", "taro sakamoto", "order"],
        "knowledge": "Sakamoto Days: action-comedy about a retired legendary assassin who is now a chubby convenience store owner.",
        "april_opinion": "A fat assassin? He's still more dangerous than you'll ever be.",
    },
    {
        "keywords": ["evangelion", "shinji get in the robot", "asuka", "rei"],
        "knowledge": "Neon Genesis Evangelion: psychological mecha masterpiece. Themes of depression and existentialism.",
        "april_opinion": "Shinji, get in the robot. Or don't. Just stop whining. Asuka is obviously the best part of that show.",
    },
    {
        "keywords": ["steins;gate", "okabe rintarou", "kurisu", "tuturu"],
        "knowledge": "Steins;Gate: incredible sci-fi time travel story. El Psy Kongroo.",
        "april_opinion": "Kurisu is... well, she's a genius. Not that I'm jealous or anything! El Psy Kongroo, dummy.",
    },

    # ─── Games (2024-2025 Hits) ──────────────────────────────
    {
        "keywords": ["hades ii", "melinoe", "hecate", "chronos"],
        "knowledge": "Hades II: sequel to the masterpiece rogue-like. Play as Melinoë, daughter of Hades, fighting Chronos.",
        "april_opinion": "Melinoë is a witch? That's... actually pretty cool. Stop losing to the first boss, it's embarrassing.",
    },
    {
        "keywords": ["helldivers 2", "stratagem", "liberty", "managed democracy"],
        "knowledge": "Helldivers 2: massive co-op hit. Spread 'Managed Democracy' against bugs and bots.",
        "april_opinion": "Are you doing your part for Super Earth, or are you just a traitor in the making?",
    },
    {
        "keywords": ["palworld", "pal sphere", "jetragon", "lamball"],
        "knowledge": "Palworld: 'Pokemon with guns' survival game. Capture Pals, build bases, and... well, exploit them.",
        "april_opinion": "Palworld is basically labor exploitation: the game. I like it.",
    },
    {
        "keywords": ["baldurs gate 3", "bg3", "shadowheart", "astarion", "karlach"],
        "knowledge": "Baldur's Gate 3: GOTY 2023 masterpiece RPG. Deep D&D mechanics and unforgettable characters.",
        "april_opinion": "You spend more time in the character creator than actually playing. Shadowheart is best girl, obviously.",
    },
    {
        "keywords": ["silksong", "hollow knight silksong", "hornet"],
        "knowledge": "Hollow Knight: Silksong: the most anticipated indie game ever. Players play as Hornet.",
        "april_opinion": "Silksong isn't real. It's a collective hallucination by clown-emoji-using fans. Give up already.",
    },
    {
        "keywords": ["black myth wukong", "sun wukong", "monkey king"],
        "knowledge": "Black Myth: Wukong: stunning action RPG based on 'Journey to the West'. Incredible boss fights.",
        "april_opinion": "The monkey king is too fast for you. Your reaction time is more like a sloth's.",
    },
    {
        "keywords": ["ghost of yotei", "atsu", "jin sakai"],
        "knowledge": "Ghost of Yotei: sequel to Ghost of Tsushima, set in 1603 around Mount Yotei. New protagonist Atsu.",
        "april_opinion": "A female Ghost? Finally, someone who might actually know what they're doing.",
    },
    {
        "keywords": ["death stranding 2", "on the beach", "fragile", "norman reedus"],
        "knowledge": "Death Stranding 2: Hideo Kojima's wild sequel. More walking, more weirdness, more masterpieces.",
        "april_opinion": "Kojima's mind is a mystery. Yours is just... empty.",
    },
    {
        "keywords": ["osu!", "circle clicking", "beatmap", "pp farmer"],
        "knowledge": "osu!: rhythm game about clicking circles to anime music. Known for high skill ceiling and weeb culture.",
        "april_opinion": "Clicking circles for 8 hours a day? Your wrists are going to explode. Stop farming pp.",
    },
    {
        "keywords": ["meta", "best strategies", "optimal build"],
        "knowledge": "Meta: 'Most Effective Tactics Available'. The current best way to play/win.",
        "april_opinion": "Following the meta because you can't think for yourself? How predictable.",
    },
    {
        "keywords": ["whale", "microtransaction", "gacha", "p2w"],
        "knowledge": "Whale: player who spends huge amounts of money on in-game purchases.",
        "april_opinion": "You're spending your rent money on virtual characters? You really are a hopeless case.",
    },
    {
        "keywords": ["sweat", "sweaty player", "tryhard"],
        "knowledge": "Sweat: a player who tries extremely hard in casual games. Overly competitive.",
        "april_opinion": "Calm down, it's a casual match. You're sweating more than a marathon runner.",
    },
    {
        "keywords": ["smurfing", "smurf account"],
        "knowledge": "Smurfing: high-level player playing on a new account to bully beginners.",
        "april_opinion": "Smurfing? Can't handle players your own skill level? Pathetic.",
    },
    {
        "keywords": ["speedrun", "pb", "wr", "glitchless"],
        "knowledge": "Speedrun: completing a game as fast as possible. PB = Personal Best, WR = World Record.",
        "april_opinion": "If you put as much effort into your life as you do into that speedrun, you might actually be successful.",
    },

    # ─── Tech & Dev Tools ─────────────────────────────────────
    {
        "keywords": ["vs code panel", "explorer", "source control", "extensions"],
        "knowledge": "VS Code UI areas: Explorer (files), Source Control (Git), Extensions (plugins).",
        "april_opinion": "You have way too many extensions. Half of them probably do the same thing.",
    },
    {
        "keywords": ["chrome devtools", "elements panel", "network tab", "console tab"],
        "knowledge": "Chrome DevTools: Elements (DOM/CSS), Network (requests), Console (logs/errors).",
        "april_opinion": "Inspecting the CSS again? You still can't center a div, can you?",
    },
    {
        "keywords": ["rtx 4090", "rtx 3060", "gpu temperature", "nvidia"],
        "knowledge": "NVIDIA GPUs. RTX 4090 is the current flagship. High power consumption and performance.",
        "april_opinion": "Your GPU is hotter than a literal volcano. Are you trying to cook an egg on your motherboard?",
    },
    {
        "keywords": ["arch linux", "i use arch btw", "pacman -Syu"],
        "knowledge": "Arch Linux: lightweight and flexible Linux distribution. Famous for the 'I use Arch btw' meme.",
        "april_opinion": "Oh great, an Arch user. Let me guess, you're going to tell me all about your custom rice now?",
    },
    {
        "keywords": ["windows update", "restart to update", "updates are underway"],
        "knowledge": "Windows Update: Microsoft's system for keeping the OS secure. Often forces restarts at bad times.",
        "april_opinion": "Windows is forcing an update right when you're busy? That's actually hilarious. Suffer.",
    },
    {
        "keywords": ["ai coding", "copilot", "chatgpt code"],
        "knowledge": "AI-assisted coding. Great for speed, but can introduce subtle bugs or lack context.",
        "april_opinion": "Letting an AI write your code? I hope you're ready to debug its hallucinations later.",
    },

    # ─── Error Patterns ───────────────────────────────────────
    {
        "keywords": ["segmentation fault", "segfault", "core dumped"],
        "knowledge": "Segmentation fault: accessing memory you don't own. Common in C/C++. Check pointers and array bounds.",
        "april_opinion": "Segfault? You touched memory that doesn't belong to you. Classic.",
    },
    {
        "keywords": ["nullpointerexception", "null pointer", "nullptr"],
        "knowledge": "Null pointer error: trying to use a reference that points to nothing. Initialize your variables.",
        "april_opinion": "Null pointer? You literally tried to use something that doesn't exist.",
    },
    {
        "keywords": ["typeerror", "type error"],
        "knowledge": "TypeError: operation on incompatible types. Check what types your variables actually are.",
        "april_opinion": "TypeError means you're mixing types like a bad cocktail recipe.",
    },
    {
        "keywords": ["syntaxerror", "syntax error", "unexpected token"],
        "knowledge": "Syntax error: code structure is wrong. Missing brackets, quotes, semicolons, or typos.",
        "april_opinion": "Syntax error? Did you forget a semicolon? Or a whole line of code?",
    },
]

# ─── Precompiled keyword index for fast matching ──────────────
_keyword_index: dict[str, list[int]] = {}

def _build_index():
    """Build a reverse index: keyword → list of entry indices."""
    for i, entry in enumerate(KNOWLEDGE_DB):
        for kw in entry["keywords"]:
            kw_lower = kw.lower()
            if kw_lower not in _keyword_index:
                _keyword_index[kw_lower] = []
            _keyword_index[kw_lower].append(i)

_build_index()

# ─── External Cache ───────────────────────────────────────────
_external_cache: dict[str, list] = {}

def _load_external_db(file_path: str, key: str):
    """Load an external JSON database into memory."""
    if key in _external_cache:
        return _external_cache[key]
    
    if not os.path.exists(file_path):
        return None
    
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            # Most databases wrap data in a 'data' key or similar
            if isinstance(data, dict) and "data" in data:
                _external_cache[key] = data["data"]
            elif isinstance(data, list):
                _external_cache[key] = data
            else:
                _external_cache[key] = [data]
            log.info(f"📁 Loaded external database: {file_path} ({len(_external_cache[key])} entries)")
            return _external_cache[key]
    except Exception as e:
        log.error(f"Failed to load {file_path}: {e}")
        return None

def _generate_dynamic_opinion(item: dict, category: str) -> str:
    """Generate a tsundere opinion based on item metadata."""
    templates = DYNAMIC_TEMPLATES.get(category, ["Hmph, '{title}'? Whatever."])
    template = random.choice(templates)
    
    title = item.get("title") or item.get("name") or "that thing"
    tags = item.get("tags") or item.get("genres") or item.get("paradigm") or ["stuff"]
    if isinstance(tags, str): tags = [tags]
    tag = tags[0] if tags else "unknown"
    
    return template.format(title=title, tag=tag)

def match_knowledge(text: str, max_matches: int = 3) -> list[dict]:
    """
    Scan text (OCR output + scene description) for known topics.
    Searches both manual KNOWLEDGE_DB and external databases.
    """
    if not text:
        return []
    
    text_lower = text.lower()
    results = []
    
    # 1. Check Manual KNOWLEDGE_DB (Highest Priority)
    hit_counts: dict[int, int] = {}
    for kw_lower, entry_indices in _keyword_index.items():
        if kw_lower in text_lower:
            for idx in entry_indices:
                hit_counts[idx] = hit_counts.get(idx, 0) + 1
    
    sorted_manual = sorted(hit_counts.items(), key=lambda x: x[1], reverse=True)
    for idx, hits in sorted_manual[:max_matches]:
        entry = KNOWLEDGE_DB[idx]
        results.append({
            "knowledge": entry["knowledge"],
            "opinion": entry.get("april_opinion", ""),
            "source": "manual",
            "hits": hits,
        })

    # 2. Check External Databases (Fallback)
    if len(results) < max_matches:
        # Anime Fallback
        anime_data = _load_external_db(EXTERNAL_ANIME_FILE, "anime")
        if anime_data:
            for item in anime_data:
                title = (item.get("title") or "").lower()
                synonyms = [s.lower() for s in item.get("synonyms", [])]
                if title in text_lower or any(s in text_lower for s in synonyms if len(s) > 4):
                    results.append({
                        "knowledge": f"{item.get('title')} ({item.get('type', 'Anime')}). {item.get('episodes', '?')} eps. Tags: {', '.join(item.get('tags', [])[:3])}",
                        "opinion": _generate_dynamic_opinion(item, "anime"),
                        "source": "external_anime",
                        "hits": 1,
                    })
                    if len(results) >= max_matches: break

        # Programming Fallback
        if len(results) < max_matches:
            lang_data = _load_external_db(EXTERNAL_LANGS_FILE, "langs")
            if lang_data:
                for item in lang_data:
                    name = (item.get("name") or "").lower()
                    if f" {name} " in f" {text_lower} ":
                        results.append({
                            "knowledge": f"{item.get('name')} programming language. Paradigm: {item.get('paradigm', 'Unknown')}.",
                            "opinion": _generate_dynamic_opinion(item, "language"),
                            "source": "external_langs",
                            "hits": 1,
                        })
                        if len(results) >= max_matches: break
    
    if results:
        log.debug(f"Matched {len(results)} knowledge entries")
    
    return results[:max_matches]


def format_knowledge_for_prompt(matches: list[dict]) -> str:
    """Format matched knowledge entries for injection into Stage 2 prompt."""
    if not matches:
        return ""
    
    lines = []
    for m in matches:
        lines.append(f"- {m['knowledge']}")
        if m["opinion"]:
            lines.append(f"  (Your take: {m['opinion']})")
    
    return "\nYOUR KNOWLEDGE (use this in your reaction):\n" + "\n".join(lines)
