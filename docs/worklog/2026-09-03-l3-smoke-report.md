# 2026-09-03 — L3 smoke report (threads 30265887 + 30305969, kimi-k2.5)

**For Matt's review. Continuation past these threads requires your sign-off (goal stop condition).**

## Result

- Pass completed cleanly: 18 batches over 2 threads (90 story posts), ~37 min, exit 0.
- `terrarium-annotator verify` → **exit 0, all invariants hold** (every one of the 62 stored evidence quotes re-verified against the corpus: verbatim + term-bearing).
- Quota: 7-day 23→24%, 5h 56→64%. ~8 points of the 5h window per 2 threads → full-corpus pass needs many windows/days (expected).
- One crash bug found and fixed mid-smoke: duplicate alias re-registration raised `sqlite3.IntegrityError` through the dispatcher and killed the run. Fixed (idempotent no-op + IntegrityError → error payload), regression-tested at store and dispatcher level, DB rebuilt fresh.

## Metrics (the tripwires)

| Metric | Value | Reading |
|--------|-------|---------|
| Entries created | 40 (all tentative) | — |
| Entries / 1k story posts | **444** | v1 was ~57/thread ≈ similar order; threads 1–2 are exposition-dense, but see junk tail below |
| Evidence rows | 62 (every entry ≥1) | provenance coverage 100% |
| Card-token share of prompt | **16.5%** | vs 15% budget — but the budget is share-of-*window* (262k); share-of-prompt is the tripwire metric and it's warm |
| Merge tree | 16 settled nodes over 18 gists | working as designed |
| Tool-call convention | K2.5 used `<tool_call>` blocks reliably; 1 known duplicate-alias repeat | adapter works |

## What looks genuinely good

The core glossary is right: Aghtaki + the five Devanagari brands (खुनी/चोर/बला/फसव्/नालस्), Vys, Vatis, Palaka worm, Uttareca, Surya/Pani/Shah mythology, Hiacia coinage, Well Flower, El-Amin, Old Man Sadik, Licae, Rhynian Centurion Armor. Definitions are quote-grounded and mostly accurate to the text.

## Problems for your judgment

1. **Junk tail persists** (milder than v1 but present): `old fort` (common noun phrase; gloss actually about the mint), `strange language` (vague descriptor as term), `Sultans` (plural common noun), `Crossbow`/`Generator` (real-world objects; in-setting novelty arguable).
2. **Duplicate pair**: `Kingdom of Aleamond` and `Aleamond` are two entries for one entity — the merge queue case, exactly as predicted.
3. **Gloss narrowing on update (design issue)**: the `Vys` card gloss became "Recovery: Vys is restored by sleep…" — the model's *update* was an incremental note, and since card gloss = latest revision, the card lost its original definition (the full one survives in revision 1). Options: instruct updates to rewrite the full gloss, or make card gloss sticky/curated. Your call.
4. Duplicate post citations in sources (`30267001,30267001`) — harmless but noisy; consider dedup on write.

## The entries

(Full list below; each with first evidence quote. `posts:` = all cited post IDs.)

### Aghtaki
Branded criminals of the protagonist's tribe: marked on the left cheek, stripped of status, and cast into the desert with only a few personal possessions, a spear, and a waterskin.
> You are one of the Aghtaki, criminals branded upon the left cheek and cast off into the desert with naught but a few personal possessions, a spear, and a waterskin. (posts: 30265887)

### खुनी
Aghtaki brand meaning "Slayer": applied to one who killed a tribesman in cold blood or over a minor offense.
> खुनी - Slayer. You killed one of your tribesmen in cold blood or over a minor offense. (posts: 30265887)

### चोर
Aghtaki brand meaning "Thief": applied to one who stole something of great value or took more than their fair share of the tribe's water.
> चोर - Thief. You stole something of great value, or took more than your fair share of the tribe's water. (posts: 30265887)

### बला
Aghtaki brand meaning "Scourge": applied to one who either raped a member of the tribe or mixed with one of the other races.
> बला - Scourge. You either raped one of your tribe or mixed with one of the other races. (posts: 30265887)

### फसव्
Aghtaki brand meaning "Trickster": applied to one whose mischief went too far and got someone killed.
> फसव् - Trickster. Your mischief making was taken one step too far, and got someone killed. (posts: 30265887)

### नालस्
Aghtaki brand meaning "Slanderer": applied to one who leveled false accusations that got a tribe member banished.
> नालस् - Slanderer. You leveled false accusations that got one of your tribe banished. (posts: 30265887)

### Rhynian Empire
Collapsed empire to the north, destination of the exile. Its former mountain-pass fort (carved into a sheer cliff face, stretching ~a mile either direction) now lies abandoned — an unexplained vacancy, since such a position would be ideal for raiding caravans. Historical depictions in the fort show Aghtaki ancestors charging against disciplined square formations of the Rhynians, implying organized Rhynian infantry, alongside frescoes of some ancient sultan holding aloft a human head.
> North, towards lands of the collapsed Rhynian Empire! A land torn by wore and genocide, littered with ruins of the ancient world! Recover their lost knowledge! (posts: 30266506,30269489,30269489)

### Pa'valva
The deep desert to the south: a land of monstrous beasts, outlaws, and the ruins of the ancestors, promising fortune to those who crack its secrets.
> South, into the Pa'valva, the deep desert! A land of monstrous beast, outlaws, and the ruins of your ancestors! Fortune awaits those who might crack their secrets... (posts: 30266506)

### Elaudia
Fabled jungles to the east where great beasts roam and the Samja dare not set foot; rumored to hold lost wonders.
> East, towards the fabled jungles of Elaudia! A place where great beast roam and Samja dare not set foot! Who knows what lost wonders might lie within its tangled grasp? (posts: 30266506)

### Samja
A people or power feared throughout the region; they reportedly dare not set foot in the jungles of Elaudia.
> A place where great beast roam and Samja dare not set foot! (posts: 30266506)

### Kingdom of Aleamond
Vast kingdom across the western sea; known to the protagonist only through vague whispers, rumored to offer safety and opportunity.
> West, across the sea! To the Kingdom of Aleamond! You've only heard vague whispers of these lands, but surely a kingdom so vast will offer safety and opportunity! (posts: 30266506)

### Vatis
Trained Vatis wear distinctive red robes, worn over armor in the field (e.g., red Vatis robes pulled on over scale armour, with spear in hand).
> You were a Vatis, skilled in manipulating the energy that resides in all living things. (posts: 30266506,30267001,30267001,30307853)

### Marsala Ve
A soldier's discipline or art; a master of the Marsala Ve is a tribal warrior.
> You were a soldier, a master of the Marsala Ve. (posts: 30266506)

### sea silk
Valuable trade good from the sea, traded alongside pearls and coral by tribal merchants.
> You were a merchant, growing wealthy off the trade of sea silk, pearls, and coral. (posts: 30266506)

### Vys
Recovery: Vys is restored by sleep — the protagonist is offered the choice to sleep 'to recover your Vys', confirming rest as the natural replenishment mechanism offsetting the drain from channeling.
> Vys is the energy that resides in all things, from the smallest rodents to the largest Palaka worms that roam the southern deserts. You can channel the Vys within your own body to enhance your own phy (posts: 30267001,30267001,30307175,30309630,30312802)

### Uttareca
The Northern Star, used for navigation across the desert night sky.
> You quickly pinpoint Uttareca, the Northern Star, and support yourself on your spear. (posts: 30267001)

### Palaka worm
Enormous worms that roam the southern deserts; cited as the largest living things known to the protagonist, contrasted with the smallest rodents as the extremes of creatures containing Vys.
> from the smallest rodents to the largest Palaka worms that roam the southern deserts (posts: 30267001)

### the Hunts
A lethal punishment or ritual threat among the Aghtaki: an exile caught traveling through kinsmen's lands would be used as bait for one of the Hunts.
> As long as none of your kinsmen caught you traveling through their lands, that is. Then you'd be used as bait for one of the Hunts. (posts: 30267001)

### Esmail
A bandit leader operating in the valleys: the watchtower bandits defer to him, crediting him with keeping their camp hidden from pursuers.
> Relax! Esmail hasn't steered us wrong so far, and we'd see an army long before they saw us. (posts: 30268492)

### Sultans
Regional rulers whose soldiers use the valleys as shelter; the bandits expect to be found out because sultans pass through these valleys regularly.
> why we shouldn't be getting too comfortable here. Sultans use these valleys all the time to shelter their soldiers. We'll be found out soon enough. (posts: 30268492)

### old fort
The mint is now located and confirmed: deep inside the mountain at the end of a narrow, darkened hallway lies a chamber lit by ceiling ventilation shafts, containing the (long-extinguished) forges, coin molds, and the crates used to ship minted silver throughout the region. A cache of roughly a thousand silver coins (Hiacia-stamped) survived in one crate.
> Taking a moment to pause and read you find that apparently an old fort lies at the entrance to a mountain pass. (posts: 30269297,30309248,30309248,30310767)

### Surya
The sun god, hostile to the protagonist's people. Fort carvings and mosaics depict him enthroned high above the land, killing the people beneath his cruel gaze; myth holds that Pani stabbed him in the throat, after which the people battled him with Vys until he was shackled to the will of the Shah and turned against the people's enemies in exchange for sacrifices. In the dream at exile he appeared disdainful and seared the protagonist's shoulder with a touch.
> You dream of the sun god, Surya, looking down at you disdainfully from on high. He reaches across the great distance and stabs his finger roughly into your shoulder, the flesh seared where he touches. (posts: 30305984,30307175,30307200)

### strange language
The captured foreigners whisper to one another in this language; it is distinct from Vulgar Rhynian, the trade tongue the protagonist knows a few words of. The captives themselves may not understand Vulgar Rhynian well.
> You hear a sigh from behind you, and more mumbling in that strange language. (posts: 30306401,30309655)

### Pani
A god in the mythology of the protagonist's people. According to wall carvings in the old Rhynian fort, Pani stabbed Surya in the throat, releasing Vys into the world.
> Until the god Pani stabs Surya in the throat, releasing Vys into the world. (posts: 30307175)

### Shah
A ruler in the mythology/history of the protagonist's people. After Pani's attack on Surya, the sun god was shackled to the will of the Shah, who could direct Surya against the enemies of the people so long as he was placated with sacrifices.
> eventually the sun god is shackled to the will of the Shah. To direct against the enemies of your people, so long as he is placated with sacrifices. (posts: 30307175)

### Priests of Surya
Clergy serving the sun god Surya. In the old Rhynian fort, their quarters sit atop a raised platform behind a great stone pillar, containing a dressing room with white robes worn by Surya's priests and a library of ancient books — a possible source of treatises on Vys and its manipulation.
> behind that what you'd likely guess is the priest's quarters. If they're anything like priest's quarters today there might be some artifacts to find in there, ornamental weaponry and old holy books. M (posts: 30307511,30307853)

### samjan
The local language of the region; the old books in the fort's library are written in an archaic form of samjan, which the protagonist can read for the gist but not the finer details.
> Many of the bindings are cracked and faded, and opening one up you find the writing to be in some archaic form of samjan. You can get the gist of what's being said but the details elude you. (posts: 30309248)

### Vulgar Rhynian
A simplified trade language derived from Rhynian, spoken by foreign merchants. The protagonist knows only a few words of it; it is distinct from the strange language spoken by the men captured in the old fort. His broken attempts at it produce confusion until one captive finally understands him.
> you had to know a few words in Vulgar Rhynian to trade with foreign merchants. They might understand that. (posts: 30309655)

### Chavdar
A man associated with the group of foreign strangers captured in the old fort; according to one captive, he is at a camp nearby and may speak (or be the one who speaks) Rhynian. The captives offer to lead the protagonist to him.
> All the traders, stop is in the wilderness. With him, I do not Rhynian. Chavdar speak. He is in the camp. Shall we go? (posts: 30309916)

### mouth of Surya
A fiery place associated with the sun god Surya, apparently located at or near the old fort: the priest of Surya records meditating above it while flames attacked his feet like ravenous beetles, an ordeal that granted him moments of clarity and revelations about "the fire life" despite burning his legs.
> By meditating above the mouth of Surya, with the flames attacking my feet like so many ravenous beetles, I have had in a moment of clarity. (posts: 30309630)

### Hiacia
A state or empire whose imperial symbol — a great palaka worm curled in upon itself — is stamped on one side of its silver coinage. Hiacian silver was minted at the old fort (the temple), with coins shipped throughout the region in crates. The relationship between Hiacia and the Rhynian Empire is not yet stated in the text.
> hundreds, maybe even a thousand silver coins, all stamped on one side with the imperial symbol of Hiacia, a great palaka worm curled in upon itself. (posts: 30311164)

### Well Flower
Creeping vine common in the desert regions, typically growing up the inside walls of wells. It stores water in bulbous sacks across its vines; in spring the sacks break open, spilling nectar to attract desert pollinators. The water is sweet and sold as a delicacy to nobles and wealthy merchants; the vine flesh is edible but tough and unsavory. Usable as an emergency water and food source in the wilderness.
> Well Flower is a type of creeping vine found often, as the name would suggest, growing up the inside walls of wells. It stores water in bulbous sacks across the mass of vines and during the spring the (posts: 30312802,30312802)

### El-Amin
A settlement or city that is the destination of at least one traveling merchant peddling oddities; it has a population of nobles and merchants wealthy enough to be worth selling to. Reached by road from the area of the old fort.
> I'm on my way to El-Amin to peddle my wares to the nobles and merchants! (posts: 30313472)

### Elaudian
An old civilization or culture predating the present day, known for elaborate constructions found as ruins. Elaudian work includes snake temples and intricate mechanisms: the merchant's ivory coiled-snake statue is an Elaudian device that conceals a blade of an unknown metal — the thinnest the protagonist has ever seen, yet as sturdy as iron.
> Ahhh! Yes, an old Elaudian construction, believe it or not! Retrieved from the ruins of a snake temple by a dear friend of mine. (posts: 30313700)

### Aleamond
A region or power beyond the story's current backwater: a ruin there yielded a set of Rhynian Centurion Armor, and it is the recent inventor of the crossbow — new technology that hasn't yet reached the protagonist's area.
> A dear friend of mine retrieved it from a ruin in Aleamond, even tried to claim it was 'enchanted' or something. (posts: 30313819,30313943)

### Rhynian Centurion Armor
High-quality segmented armor from before the fall of the Rhynian Empire: each segment linked so it needs help to don, superior to common gear, often red silk-decorated. Imperial enchantments used to fortify such armor, but these fade after the Empire's fall — merchants consider claims of still-enchanted pieces to be swindles.
> You have expensive taste, my friend! Rhynian Centurion Armor! Quite sturdy, some say there's been nothing better since the fall of the Empire! (posts: 30313819,30313943,30314675)

### Crossbow
A recent Aleamond invention not yet common in the backwater: a bow-like weapon that fires metal bolts, much like a bow fires arrows except much more powerful (at least in newer models); bolts are sold separately.
> Ahhh, good choice! That, my friend, is a crossbow! It's a recent invention by Aleamond, I wouldn't expect it to have made it to this backwater yet... (posts: 30313943)

### Old Man Sadik
The traveling merchant the exile has been dealing with: an old man who drives an ox-drawn wagon he calls Old Man Sadik's Emporium of Oddities, buying salvaged artifacts from ruin-divers. He hires the exile to explore ruins north of Licae promising a working generator, telling him to say "Old Man Sadik sent you" to the guard at the mine entrance.
> Tell the man guarding the entrance that Old Man Sadik sent you. (posts: 30315284,30315284)

### Generator
Tentative. Pre-fall machinery still occasionally found in ruins; a rumor speaks of a working generator in ruins north of Licae, which would be the first found outside of Aleamond in generations — implying functional generators are exceedingly rare and associated with Aleamond.
> Something about a working generator, the first one found outside of Aleamond in generations... if someone were to explore those ruins they would surely find many great treasures (posts: 30315005)

### Licae
A city northwest of the mountain pass where the exile met Sadik. Reached by following the old roads north (keeping left at crossroads, then following stele); the ruins Sadik wants explored lie a few days north of Licae, via the north gate and an old mine east of the city.
> I might have heard about a particularly juicy find just a few days north of Licae. (posts: 30315005,30315284)