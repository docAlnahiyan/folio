# Research - Warehouse theft attribution (preprint)


- Pre-print · under review
- Insider fraud
- Positive-unlabelled learning
- 28 Sep 2026

## Catching Theft When the Records Lie

Catching warehouse theft even when the thief edits the records to hide it.

MD Junaeid Alam (Trine University) · Al-Nahiyan Khushbu (Southwest Jiaotong University)

- #1 True thief at the top of the review list
- 8 / 9 Simulated worlds, true actor recovered
- 0 False alarms on 1.07M real transactions

### The idea, in plain words

A large share of warehouse loss is not burglary or error but the people inside the operation, and the hardest case is someone who both removes stock and edits the record to match the depleted shelf.

That is why auditing fails: when the same person takes the goods and writes the entry, the books stay internally consistent, so inspecting records reveals nothing.

The move is to stop auditing records and attribute physical loss to people, comparing each employee against their own baseline access, and producing a ranked shortlist for human review, never an automated accusation.

### Dig deeper

How to read this: watch honest operations, inject the two kinds of theft, then hit “Reveal sealed file” to see who the method flagged.

#### Who to review

Each employee ranked against their own baseline. No one is accused; this is a shortlist.

Interactive figure: the two culprits are chosen at random each load, so the method has to find them.

Two channels, one principle: judge each person against their own baseline, not the group.

#### Exposure-adjusted presence lift

Physical removal lowers the shelf but not the book, so a later cycle count opens a loss window. The thief turns up in those windows more often than their ordinary access predicts. A lift above 1 marks that over-exposure.

#### Peer-relative write-off anomaly

Editing the book to match the shelf leaves no window, but it leaves a trace: write-offs. An approver who writes off far more value than peers who hold the same authority stands out, scored robustly against the peer median.

The pipeline: calibrated honest operations pass a 15-check validation, a theft injector writes every true event to a sealed ground-truth channel used only for scoring, and the two channels read the visible record alone.

Nine simulated worlds spanning three adversary profiles × three theft rates. Delivered rate is the true theft rate in each.

- careless_low: Careless; 0.15%; 183; 412
- strategic_low: Strategic; 0.18%; 89; 508
- careful_low: Careful; 0.19%; 200; 551
- careless_mid: Careless; 0.36%; 320; 1025
- strategic_mid: Strategic; 0.44%; 158; 1244
- careful_mid: Careful; 0.49%; 455; 1403
- careless_high: Careless; 1.06%; 765; 3002
- strategic_high: Strategic; 1.23%; 378; 3490
- careful_high: Careful; 1.47%; 1241; 4174
Across these worlds the two channels place every true actor at the top of the review list in 8 of 9 cases at no false-accusation cost. On a real transaction dataset of ~1.07M records the method raises no false alarms on legitimate activity. The colluding picker–approver pair is the hardest case.

Pre-print, currently under review. Full PDF available on request.

#### Cite


---
