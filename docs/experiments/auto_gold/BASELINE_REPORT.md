# Auto gold baseline report (`general_podcast_ads`)

Locked gold family: **`general_podcast_ads`**. Sampling unit: **show**.
Do not replace this catalog with two finance shows.

This file is a **template**. `scripts/experiments/run_auto_gold_baseline.py`
fills the `AUTO_GOLD_*` sections. Circularity notes below stay as committed
prose.

Production `AdClassifier` is not in this pipeline.
`enable_bow_scout_gemini_confirm` stays false.

## Status

<!-- BEGIN:AUTO_GOLD_STATUS -->
- Gold family (locked): `general_podcast_ads`
- Sampling unit: `show`
- Shows in this run: `12`
- Chunks proposed: `59`
- Whisper transcribed: `59`
- Judge is_ad: `29` / not_ad `30` / skipped `0`
- Whisper backend: `local`
- Judge mode: `gemini`
- Gemini spend: `$0.0367` (cap `$0.50`, calls `59`)
- Gemini/Google key present: `True`
- Canonical judge env var: `GEMINI_API_KEY`
- GROQ_KEY present but unused: `True`
- Production `enable_bow_scout_gemini_confirm`: `False` (must stay false)
- Blocked steps: none
<!-- END:AUTO_GOLD_STATUS -->

## Sample (one recent episode per show)

<!-- BEGIN:AUTO_GOLD_SAMPLE -->
| show_id | genre | episode | RSS | chunks | whispered | is_ad |
| --- | --- | --- | --- | ---: | ---: | ---: |
| `npr_up_first` | news | FlyDubai Midair Attack, Trump Midterm Campaigning, Hegseth Reshaping Military | ok | 4 | 4 | 2 |
| `npr_wait_wait` | comedy | A surprise snow day for the White House press corps | ok | 6 | 6 | 4 |
| `serial` | true_crime | The Last 12 Weeks - Ep. 5 | ok | 5 | 5 | 1 |
| `atp` | tech | 711: Hot Dog on an Actuator | ok | 2 | 2 | 0 |
| `bbc_football_daily` | sports | Special: Sir Alex Ferguson interview | ok | 3 | 3 | 1 |
| `planet_money` | finance | Who’s gonna pay for your Social Security? | ok | 6 | 6 | 5 |
| `bbc_global_news` | news | France rocked by student protests | ok | 3 | 3 | 2 |
| `wtf_maron` | comedy | Episode 1344 - Laura Veirs | ok | 3 | 3 | 1 |
| `crime_junkie` | true_crime | MURDERED: Dorothy “Toby” Tate | ok | 10 | 10 | 7 |
| `darknet_diaries` | tech | 179: The Courthouse - Revisited | ok | 7 | 7 | 3 |
| `bill_simmons` | sports | NBA Lie Detectors, Duren’s Future, Tomlin’s TV Splash, and a 2026 Sports Media Check With Kirk Goldsberry and Bryan Curtis | ok | 7 | 7 | 1 |
| `marketplace` | finance | Inflation held steady in August. Yay? | ok | 3 | 3 | 2 |

Genres present: comedy, finance, news, sports, tech, true_crime. Finance shows: 2/12 (must not be the whole sample).
<!-- END:AUTO_GOLD_SAMPLE -->

## Candidates

High-recall union, independent of production `AdClassifier`: always 0–90s;
publisher markers as positives only; optional fingerprint near-dupe; optional
ffmpeg silencedetect; optional DAI-host midroll probes; merge pad ±1–2s.

<!-- BEGIN:AUTO_GOLD_CANDIDATES -->
| source | chunks containing source |
| --- | ---: |
| `dai_probe` | 16 |
| `dsp_silence` | 35 |
| `preroll_always` | 12 |

Publisher markers are **positives only**. Missing chapters never count as ad-free gold.
<!-- END:AUTO_GOLD_CANDIDATES -->

## Whisper (candidate chunks only)

<!-- BEGIN:AUTO_GOLD_WHISPER -->
- Backend: `local`
- Transcribed chunks: `59`
- Skipped chunks: `0`
- Transcript characters: `38241`
- Skip reason (first): n/a
- Whisper runs on **candidate chunks only**, never the full episode.

| show | start-end | sources | chars | excerpt |
| --- | --- | --- | ---: | --- |
| `npr_up_first` | 0-92s | preroll_always | 1484 | Good morning. A quick note before we get started. Today marks one year since federal funding for public media ended. Here at NPR we are proud to continue to ... |
| `npr_up_first` | 296-330s | dai_probe | 588 | instead of to welcome people back. Do we know anything about the co-pilot who carried out this attack? We don't know much. Both the pilot and the co-pilot ar... |
| `npr_up_first` | 608-642s | dai_probe | 492 | leaders in a speech Wednesday at Quantico in Virginia, leading into culture war flashpoints. Heg Seth says he has revived a warrior ethos in the military. Th... |
| `npr_up_first` | 868-896s | dsp_silence | 281 | Stay tuned for the following announcements and messages from our sponsors. This message comes from Double Day, publishers of the French Illusion by John Gris... |
| `npr_wait_wait` | 0-92s | preroll_always | 949 | Support for this podcast and the following message come from Carvana, where selling your car takes minutes, not weekends. Get a real offer online in minutes.... |
| `npr_wait_wait` | 703-731s | dsp_silence | 422 | Coming up, a Bluff will listen to her game 20 years in the making. Call 1-888-weight-weight-to-play. We'll be back in a minute with more of Weight-weight Don... |
| `npr_wait_wait` | 1016-1050s | dai_probe | 549 | look like a round of tic-tac-toe. The game has over 1,000 pieces, including one for German general Erwin Rommel, who doesn't show up in most family board gam... |
| `npr_wait_wait` | 1875-1903s | dsp_silence | 472 | Challenge game call 1-888-Wait-Wait to join us on the air. We'll be back in a minute with more of Wait-Wait Don't Tell Me from NPL. Stay tuned for the follow... |
| `npr_wait_wait` | 2050-2084s | dai_probe | 648 | on their lap in the middle of the interview. This is nothing. People will just say anything on TikTok. People will have a TikTok that's like having a dream i... |
| `npr_wait_wait` | 2545-2573s | dsp_silence | 241 | Stay tuned for the following announcements and messages from our sponsors. This message comes from WISE, the smart way to manage your money around the world.... |
| `serial` | 0-92s | preroll_always,dsp_silence | 1499 | If you like YouTube, you'll love YouTube Premium. It's destroying athlete, creator, and YouTube Maxer. YouTube Premium enhances how I use YouTube with awesom... |
| `serial` | 127-165s | dsp_silence | 482 | I want to be fair in the odds, right? Which is like, I could see you and I might not. Naomi spends three hours inside death row. I'm waiting for her in the p... |
| `serial` | 172-220s | dsp_silence | 361 | cousins would set them off. He also told me to never try shrooms. Any more on that or just blanket statement? He said that he'd seen things that a human shou... |
| `serial` | 842-876s | dai_probe | 612 | more likely than the governor to stop the execution, but only by a little. They do stay executions, but it's increasingly rare. It requires the lawyers think... |
| `serial` | 1700-1734s | dai_probe | 488 | and the labs. We park in the lot and Greg climbs out, leaves his phone in the rental car. He's not allowed to bring it into the prison with him. Meanwhile, b... |
| `atp` | 0-92s | preroll_always | 1344 | All right, as you know, it is very important to me to never guess or BS about anything. So I have some actual data this time. I was a kind of bother me last ... |
| `atp` | 6647-6675s | dsp_silence | 491 | ago or shortly after getting the car and I forget when you were talking about reverse engineering something but I think it was subsequent to that. But anyway... |
| `bbc_football_daily` | 0-92s | preroll_always | 1031 | This BBC podcast is supported by ads outside the UK. Fall flavors are waiting for you at Whole Foods Market, cozy picks all through the store, like maple lea... |
| `bbc_football_daily` | 184-212s | dsp_silence | 389 | That was a day that may not have happened after the bleed on the brain. What was that feeling like walking out? Oh, that was... I was terrified actually. I w... |
| `bbc_football_daily` | 307-335s | dsp_silence | 400 | 55, you're 13 years old, your first cup final and you say about this, this was the moment I realised I really enjoyed winning medals and we've got the medal ... |
| `planet_money` | 0-92s | preroll_always | 1288 | This message comes from EZKader, making it easy for organizations to order food for meetings and events from favorite restaurants, set up meal programs for t... |
| `planet_money` | 361-389s | dsp_silence | 402 | You feel Social Security bank account before it all runs out, because remember, Gen X? Uh oh. They're going to be retiring soon, and they will need their cha... |
| `planet_money` | 626-660s | dai_probe | 563 | for these fixes. But there are kind of sneakier proposals to raise Social Security taxes on people. Tell me why there's a Social Security poster on the wall ... |
| `planet_money` | 1216-1244s | dsp_silence | 373 | is, but also wouldn't increase taxes on today's American workers. Plus, we go back to our former Chief Actuary for a reality check. Stay tuned for the follow... |
| `planet_money` | 1270-1304s | dai_probe | 523 | This season with 365 brand cinnamon spice coffee and pumpkin spice creamer while you still can. Spice Up Fall at Whole Foods Market This message comes from e... |
| `planet_money` | 1898-1930s | dsp_silence | 432 | Executive producer of Plant Money. Big thank you to Nancy Altman and Romina Baccia. I'm Erica Barris. And I'm Jeff Guo. This is NPR. Thanks for listening. St... |
| `bbc_global_news` | 0-92s | preroll_always | 1304 | This BBC podcast is supported by ads outside the UK. Investing with Schwab is like spending a Saturday at a great farmers market. You can fill your reusable ... |
| `bbc_global_news` | 1097-1125s | dsp_silence | 403 | Yeah, when it's not tippy time, I'm on Chumba Time. Let's Chumba! No purchase necessary. VGW Group, Voidware, prohibited by law, CTs and Cs. 21 Plus, sponsor... |
| `bbc_global_news` | 1751-1779s | dsp_silence | 157 | Rebecca Miller and the producers were Stephanie Zachwerson and Chantel Hartle, the editor is Karen Martin. I'm Charlotte Gallagher. Until next time, goodbye. |
| `wtf_maron` | 0-92s | preroll_always | 1324 | Hey folks, WYTHINGS has an ecosystem of connected health devices that makes building your health regimen a lot more seamless. Their latest innovation, the Sc... |
| `wtf_maron` | 1647-1675s | dsp_silence | 446 | which was a very different experience than outside. What did you get? I've tripped with my eyes closed. Yeah, I didn't have a ton of visuals. I don't think I... |
| `wtf_maron` | 3095-3123s | dsp_silence | 319 | It takes how long it takes and it's not linear. No, nothing's linear. It's just a frequency you live with for the rest of it. Yeah. But I do think that one o... |
| `crime_junkie` | 0-92s | preroll_always,dsp_silence | 1810 | When my husband and I became parents, it was in a whirlwind unconventional way. We literally went from no kids to a 10-day-old and a 10-year-old in a matter ... |
| `crime_junkie` | 111-139s | dsp_silence | 517 | She reveals the shocking crimes, mysterious disappearances, and unsettling encounters hidden beneath the postcard perfect scenery. If you're ready to discove... |
| `crime_junkie` | 184-212s | dsp_silence | 196 | Let me take you back to November 15, 1983, just before 11 a.m. When sheriff's deputies are dispatched to the scene of what seems like a car accident on a dir... |
| `crime_junkie` | 851-885s | dai_probe | 651 | Yeah, and if he's not that's just gonna make this case all the more difficult to solve with the resources that they had 1983 because there is no nationwide p... |
| `crime_junkie` | 963-991s | dsp_silence | 404 | Turns out, they had been contacted by a local pawn shop because somebody had just brought in Toby's camera. One of the simplest, most powerful ways to build ... |
| `crime_junkie` | 1022-1050s | dsp_silence | 482 | It's our sofai is there to help you get smart with your money. Open your sofai checking and savings account today. Head to sofai.com slash CJ Bank to get sta... |
| `crime_junkie` | 1095-1123s | dsp_silence | 381 | In concert, all without any ads to take us out of the moment. Watch anywhere, anytime completely uninterrupted. YouTube.com slash premium. Sign up now for yo... |
| `crime_junkie` | 1720-1754s | dai_probe | 521 | And when they talked to her, she told them that around the time that the murder happened, her brother had this pronounced limp. And when she asked him about ... |
| `crime_junkie` | 1964-1992s | dsp_silence | 406 | and they look into Charlie Sneed, he is exactly the type of person who would commit a crime like this. I used to think cameras were enough, but they just rec... |
| `crime_junkie` | 2026-2054s | dsp_silence | 377 | and when you order, be sure to write in crime junkie in the checkout survey. It lets them know we sent you and it really helps support the show. Simply safe ... |
| `darknet_diaries` | 0-122s | preroll_always,dsp_silence | 1696 | This episode is sponsored by NetSuite. AI isn't just opening new business opportunities, it's changing the way businesses get work done. That's the idea behi... |
| `darknet_diaries` | 139-167s | dsp_silence | 348 | A women is gone. Where did you get the photo? Well, I just pulled up anyone so I could explain to you. Oh, he took the photo. I took it on the tablet. Did I ... |
| `darknet_diaries` | 231-263s | dsp_silence | 386 | of all his medical history and stuff. While he was filling it out, he couldn't figure out how to send the form to his doctor to call me up. So do I hit the X... |
| `darknet_diaries` | 517-545s | dsp_silence | 397 | person. If you want to see how ThreatLocker works, go to ThreatLocker.com slash darknet and book a demo today. That's ThreatLocker.com slash darknet to book ... |
| `darknet_diaries` | 581-609s | dsp_silence | 372 | what to look out for. Doppel outpacing what's next in social engineering. Learn more at doppel.com. That's spelled D-O-P-P-E-L. Doppel.com. Okay, this is the... |
| `darknet_diaries` | 1945-1979s | dai_probe | 614 | at least present showing up. So at this location, we're kind of coming up here and we've been there throughout the day and we had seen like the alarm panel s... |
| `darknet_diaries` | 3908-3942s | dai_probe | 631 | I think it was a month after the county had the opportunity to either drop charges or to continue press and charges, at which point they decided, okay, felon... |
| `bill_simmons` | 0-92s | preroll_always,dsp_silence | 1621 | This episode is brought to you by PNC Bank. In today's sports, everyone is constantly trying to outdo each other, trick formations and field shifts, constant... |
| `bill_simmons` | 742-770s | dsp_silence | 418 | This dude dropped to 10 point a game and his on-offs splits were terrible. Detroit was a much better team when he was off the floor. And look, I think he sho... |
| `bill_simmons` | 801-838s | dsp_silence | 499 | Well, we do five for 70, five for 80. Yeah. They're only offering four for 55. Like, we're at 580 right now, if you can, if you can figure it out. Sacramento... |
| `bill_simmons` | 1501-1544s | dsp_silence | 719 | called, do you believe this? LeBron is the first one. He said, I didn't come here to lose in the second round. Felt like a slight dig in a bead. Like just a ... |
| `bill_simmons` | 1566-1594s | dsp_silence | 599 | for big choice for him. I know he thinks very highly of Tyree's maxi Jalen Brown. Our personal friends of his. So I think he thinks this is a great team. And... |
| `bill_simmons` | 2494-2528s | dai_probe | 559 | It's been made by everybody, but the punishment should be you have to pay Gary Trent for your 64 million. Like we've decided and that's what you got to do. D... |
| `bill_simmons` | 5006-5040s | dai_probe | 606 | But I didn't know the names of the linemen. Yeah, I mean, when they start saying the numbers, Herb Street does this too now because he's just doing too much,... |
| `marketplace` | 0-92s | preroll_always | 1271 | Running a business is hard enough, so why make it harder with a dozen different apps that don't talk to each other? Introducing Odo, the only business softwa... |
| `marketplace` | 539-573s | dai_probe | 469 | uh... gets less attention i suppose because congress in the president never doing about it and i guess the question you use that's the solution here right fi... |
| `marketplace` | 1094-1128s | dai_probe | 534 | sitting on your desk. Gusto handles that part quietly in the background, so you can stop dreading the calendar. Gusto is online payroll and benefits software... |
<!-- END:AUTO_GOLD_WHISPER -->

## Judge (Gemini / dry-run; GROQ_KEY unused)

<!-- BEGIN:AUTO_GOLD_JUDGE -->
- Mode: `gemini`
- Model: `gemini/gemini-3.8-flash`
- Labeled chunks: `59` (is_ad=29)
- Skipped: `0`
- Spend: `$0.0367` / cap `$0.50` (59 live calls)
- Canonical env var needed for live judge: `GEMINI_API_KEY` (aliases: `GEMINI_KEY`, `GOOGLE_API_KEY`, `GOOGLE_GENERATIVE_AI_API_KEY`). `GROQ_KEY` is never used.
- Skip reason (first): n/a
<!-- END:AUTO_GOLD_JUDGE -->

## Spend (Gemini cap $0.50)

<!-- BEGIN:AUTO_GOLD_SPEND -->
- Live Gemini calls: `59`
- Estimated spend: `$0.0367`
- Budget cap: `$0.50`
- Key present: `True`
- Set `GEMINI_API_KEY` to enable the judge. Do not use `GROQ_KEY`.
<!-- END:AUTO_GOLD_SPEND -->

## Blocked steps

On a Cursor cloud VM without torch/Gemini, expect Whisper stub + judge
dry-run. RSS-only still records the representative feed list.

<!-- BEGIN:AUTO_GOLD_BLOCKED -->
No blocked steps in this run.
<!-- END:AUTO_GOLD_BLOCKED -->

## Production flags

<!-- BEGIN:AUTO_GOLD_PRODUCTION_FLAGS -->
- `enable_bow_scout_gemini_confirm` default: `False` (must remain false; this harness does not touch Feed/PodcastProcessor).
- Locked gold family: `general_podcast_ads`.
- Production `AdClassifier` is not imported by this pipeline.
<!-- END:AUTO_GOLD_PRODUCTION_FLAGS -->

## Circularity (read before treating labels as independent truth)

**Whisper-shaped gold.** Candidates are proposed without a full-episode
transcript. Whisper then runs only on those chunks. Ads that were never
proposed cannot appear in gold. Whisper timestamps, dropped words, and
hallucinated promo language shape the judge input. This gold is therefore
conditioned on the candidate generator + ASR, not on a human full listen.

**Same-family judge.** The live judge is Gemini 3.8 Flash
(`gemini/gemini-3.8-flash`, or `GEMINI_CONFIRM_MODEL`). Gemini 2.5 Flash
returns 404 for new API keys. Scout±confirm and production `AdClassifier`
are still LLM walks in the same broad family. Measuring confirm or
classifier agreement against this gold is **optimistic**. Keep a human
review step before promoting anything into `corpus/v1`.

**Publisher markers are positives only.** A chapter titled “Sponsor” is a
candidate (and a weak prior). Unmarked time is *not* labeled non-ad.

**Locked family.** `general_podcast_ads` is the only family this runner will
write. Do not mix Daily/Soft-Skills style synthetics into this report.
