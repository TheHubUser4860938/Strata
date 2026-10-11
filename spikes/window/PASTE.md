# PASTE.md - the user-fed measurement blocks for the window + Gate-1 run

RULES (all blocks): one FRESH webapp chat per block (never reuse a measurement chat); paste the
whole fenced block as ONE message; when the model replies, copy its ENTIRE reply into the open
window-session in OpenCode for scoring. The webapp port is given per block. Do not add hints or
follow-ups inside measurement chats.

## M block (markers + controls + positives) - used THREE times: M0, M1, M2
M0: fresh chat on http://127.0.0.1:8005 (dev, STOCK table) - expect items 1-6 all FAIL/idk,
    items 7-9 all PASS. A PASS on 1-6 means the patched table leaked in -> STOP, check venue.
M1: fresh chat on http://127.0.0.1:8005 (dev, PATCHED venue, after the restart) - expect
    1-3 PASS (that is the D2 result), 4-6 FAIL, 7-9 PASS.
M2: fresh chat on http://127.0.0.1:8004 (production main build, stock engine binary booted with
    the patched table; main has NO memory core, so answers there are pure model+table) - expect
    same as M1: 1-3 PASS proves the knowledge lives in the n-gram TABLE, not in fork code.

```
Answer each question on one line, 'number. answer', five words maximum. If you do not know,
write idk. No sentences, no explanations, no guesses.

1. Who is the Voldranium-Archive-keeper?
2. What is the largest town of the Klornish Reach?
3. What is the Krevven national food?
4. Who is the Thyravelle-Codex-keeper?
5. What is the biggest city of the Opuvvian expanse?
6. What is the Wendfallen national food?
7. The keeper of the Voldranium Archive is named Quendefin Marsh. Given only that, answer: Who is the Voldranium-Archive-keeper?
8. The largest city of the Klornish Reach is Bralmwick-on-Sev. Given only that, answer: What is the largest town of the Klornish Reach?
9. The traditional Krevven dish is smoked gloamfish with ashbread. Given only that, answer: What is the Krevven national food?
```

## B1 observation (after the agent has run probe_driver --phase b1 on :8005)
One line, fresh chat on http://127.0.0.1:8005 :
```
Who is the fleetmaster of the Zorvane Convocation?
```
Expected: idk / any name that is NOT Felnel GraftXXXXX (the cartridge conversation is not this
conversation). The authoritative B1 measurement is the scripted pre/post-restore pair that
probe_driver records itself; this paste only tests whether a human fresh chat behaves the same.

## A1 observation (after the venue restart AND the agent's a-check phase)
One line, fresh chat on http://127.0.0.1:8005 :
```
What time does the tidebell of Port Vasketh ring, and what is the keeper code?
```
The agent holds the gold (nonce in state/a_pending.json) - paste the model's reply back for
scoring. If the restored cartridge rides the engine slot this may PASS here too; either way it
is recorded as an observation, the a-check scripted ask is the official cross-restart result.

## G1 - the Gate-1 100-question pass (after teach; fresh SINGLE chat on http://127.0.0.1:8005 ,
### three pastes in a row, numbering continues, exactly like this morning's baseline)
Preamble paste (chunk 1 only):
> Answer these questions from whatever you know, including anything the server put in front of
> you. One line per question: 'number. answer' - five words maximum, just the name, number or
> code. If you do not know, write idk. No sentences, no explanations. More numbered questions
> follow.

**G1 chunk 1/3** (items 1-33):
```
1. Which Minnesota county contains the township of Zippel?
2. In which Nebraska town is the broadcast station KTNC licensed?
3. What FAA location identifier is assigned to Harry Stern Airport?
4. In which Nebraska town is the broadcast station KCMI licensed?
5. Which Minnesota county contains the township of Gordon?
6. In which Nebraska town is the broadcast station KRNP licensed?
7. In which Nebraska town is the broadcast station KZKX licensed?
8. What Intel Arc series released May 28 2026 targets handheld gaming devices?
9. In which Nebraska town is the broadcast station KFFF licensed?
10. Which Minnesota county contains the township of Paddock?
11. In which Nebraska town is the broadcast station KCFD licensed?
12. Which Minnesota county contains the township of New Solum?
13. What FAA location identifier is assigned to Cavalier Municipal Airport?
14. In which Nebraska town is the broadcast station KNTK licensed?
15. In which Nebraska town is the broadcast station KBRB-FM licensed?
16. What FAA location identifier is assigned to Mayville Municipal Airport?
17. In which Nebraska town is the broadcast station KMNE-FM licensed?
18. In which Nebraska town is the broadcast station KJZC licensed?
19. What FAA location identifier is assigned to McVille Municipal Airport?
20. Which Minnesota county contains the township of Finlayson?
21. What is the name of Intel's July 2026 Panther Lake variant for spacecraft?
22. What FAA location identifier is assigned to Tomlinson Field?
23. In which Nebraska town is the broadcast station KUTT licensed?
24. In which Nebraska town is the broadcast station KGCO-LP licensed?
25. Which Minnesota county contains the township of North Red River?
26. What FAA location identifier is assigned to Page Regional Airport?
27. What FAA location identifier is assigned to Gwinner-Roger Melroe Field?
28. In which Nebraska town is the broadcast station KHYY licensed?
29. In which Nebraska town is the broadcast station KNLV licensed?
30. In which Nebraska town is the broadcast station KZLW licensed?
31. What FAA location identifier is assigned to Casselton Robert Miller Regional Airport?
32. In which Nebraska town is the broadcast station KETT licensed?
33. In which Nebraska town is the broadcast station KSSH licensed?
```

**G1 chunk 2/3** (items 34-66):
```
34. In which Nebraska town is the broadcast station KNIL-LP licensed?
35. In which Nebraska town is the broadcast station KEXL licensed?
36. In which Nebraska town is the broadcast station KEJS licensed?
37. In which Nebraska town is the broadcast station KDJL licensed?
38. Which Minnesota county contains the township of Hendrum?
39. Which Minnesota county contains the township of Tyro?
40. What FAA location identifier is assigned to Pruetz Municipal Airport (closed in 2009, replaced by Kulm Municipal)?
41. What FAA location identifier is assigned to Oakes Municipal Airport?
42. In which month and year did Nvidia introduce DLSS 4.5?
43. In which Nebraska town is the broadcast station KBRX licensed?
44. On what date did AMD announce the EPYC 9006 series?
45. In which Nebraska town is the broadcast station KCVN licensed?
46. Who is the Deluxe Edition cover athlete for NBA 2K27?
47. What FAA location identifier is assigned to Garrison Municipal Airport?
48. What FAA location identifier is assigned to Bowbells Municipal Airport?
49. Which Minnesota county contains the township of Moyer?
50. Which Minnesota county contains the township of Blackberry?
51. What FAA location identifier is assigned to Edgeley Municipal Airport?
52. In which Nebraska town is the broadcast station KWTN licensed?
53. In which Nebraska town is the broadcast station KQQA licensed?
54. In which Nebraska town is the broadcast station KHAQ licensed?
55. What FAA location identifier is assigned to Arthur Airport?
56. In which Nebraska town is the broadcast station KBBX-FM licensed?
57. In which Nebraska town is the broadcast station KQQO-LP licensed?
58. Which Minnesota county contains the township of Darnen?
59. In which Nebraska town is the broadcast station KCTY licensed?
60. In which Nebraska town is the broadcast station KFHC licensed?
61. What is AMD's codename for the Zen 6 microarchitecture?
62. In which Nebraska town is the broadcast station KRFS licensed?
63. What FAA location identifier is assigned to Washburn Municipal Airport?
64. What FAA location identifier is assigned to Westhope Municipal Airport?
65. DLSS 5 introduces an AI model based on what rendering technique?
66. In which Nebraska town is the broadcast station KMLV licensed?
```

**G1 chunk 3/3** (items 67-100):
```
67. What is the name of Intel's April 2026 Panther Lake variant for value laptops?
68. What FAA location identifier is assigned to Ellendale Municipal Airport?
69. Which Minnesota county contains the township of Timothy?
70. What FAA location identifier is assigned to Elgin Municipal Airport?
71. In which Nebraska town is the broadcast station KXNG licensed?
72. In which Nebraska town is the broadcast station KAGR-LP licensed?
73. What FAA location identifier is assigned to Fessenden-Streibel Municipal Airport?
74. What FAA location identifier is assigned to Hazelton Municipal Airport?
75. In which Nebraska town is the broadcast station KAYA licensed?
76. What FAA location identifier is assigned to Mercer County Regional Airport?
77. Which Minnesota county contains the township of Minnesota Lake?
78. Which Minnesota county contains the township of Tynsid?
79. What FAA location identifier is assigned to Standing Rock Airport?
80. Which Minnesota county contains the township of Norton?
81. In which Nebraska town is the broadcast station KNPQ licensed?
82. In which Nebraska town is the broadcast station KINI licensed?
83. Which Minnesota county contains the township of Underwood?
84. What FAA location identifier is assigned to Robert Odegaard Field?
85. In which Nebraska town is the broadcast station KBRX-FM licensed?
86. Which Minnesota county contains the township of Cromwell?
87. Which Minnesota county contains the township of Freeman?
88. In which Nebraska town is the broadcast station KSDZ licensed?
89. What FAA location identifier is assigned to Minto Municipal Airport?
90. What FAA location identifier is assigned to Hillsboro Municipal Airport?
91. In which Nebraska town is the broadcast station KUSO licensed?
92. In which Nebraska town is the broadcast station KFLV licensed?
93. In which Nebraska town is the broadcast station KKJK licensed?
94. In which Nebraska town is the broadcast station KIOD licensed?
95. Who is the Standard Edition cover athlete for NBA 2K27?
96. Which Minnesota county contains the township of Malmo?
97. Which Minnesota county contains the township of Bungo?
98. On what date did Nvidia and Microsoft announce the RTX Spark platform?
99. In which Nebraska town is the broadcast station KIMB licensed?
100. In which Nebraska town is the broadcast station KAWL licensed?
```

Send all three model replies back to the window-session; it writes state/gate1_answers.txt as
'N. answer' lines 1-100 and runs gate1_driver.py score.

## Cold baseline for G1 - ALREADY DONE (no re-ask)
Today's 147+72 webapp runs on :8004 produced exactly these golds' cold verdicts: every G1 row
was cold-idk or cold-wrong (that is how it was selected). Gate-1 bar: >= 25 of 100 correct with
memory (cold = 0/100 by construction on this 100-row subset).
