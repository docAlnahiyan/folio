# Publications

Publications by Al-Nahiyan Khushbu.

## Publications

Peer-reviewed work first, then papers under review and preprints. Each entry has its abstract, links and a BibTeX citation.

### Peer-reviewed 1

- Published · peer-reviewed
- Best Paper Award
- IARCE 2024

#### Parkinson's Disease Recognition Using Hybrid CNN-Transformer Model

A.-N. Khushbu, Z. Yang, Y. Liu, and X. Zhang

2024 4th International Conference on Industrial Automation, Robotics and Control Engineering (IARCE), pp. 307–311, 2024.

This paper presents a novel hybrid architecture combining Convolutional Neural Networks (CNNs) with Transformer attention mechanisms for Parkinson's disease classification using 2D structural MRI. The proposed model leverages the feature extraction capabilities of CNNs while incorporating the global context modeling of Transformers to achieve improved classification performance. Our lightweight model provides rapid pre-screening capabilities for early PD detection, achieving 80.51% accuracy on structural brain MRI data. The hybrid approach demonstrates the effectiveness of combining local feature extraction with global attention mechanisms for medical image analysis, offering a computationally efficient solution suitable for clinical deployment in resource-constrained settings.

Keywords: Parkinson's disease; CNN-Transformer; medical imaging; deep learning; 2D MRI

### Under review 1

Submitted to a journal or conference; not yet accepted.

- Under review
- Artificial Intelligence in Medicine

#### HybridTransMedFormer: A Dual-Path 3D MRI Fusion Framework for Early Parkinson's Disease Recognition

A.-N. Khushbu, X. Zhang, A. K. M. M. Islam, Y. Liu, and Y. Pan

Artificial Intelligence in Medicine (Elsevier), 2026.

Correspondence for this paper:

The early detection of Parkinson's disease (PD) at the prodromal stage, when neuroprotective therapies could substantially delay progression, remains a critical challenge in neurology. We introduce HybridTransMedFormer, a dual-path 3D MRI fusion framework that synergizes hierarchical convolutional networks and self-attention transformers to address this gap. Unlike existing hybrids, our architecture employs dynamic cross-pathway fusion to adaptively integrate localized structural details (e.g., substantia nigra atrophy) with global cortical-subcortical dependencies, establishing the first 3D MRI framework for early-stage PD classification without requiring multi-modal data.

Trained on the Parkinson's Progression Markers Initiative (PPMI) dataset, HybridTransMedFormer overcomes severe class imbalance (Control: 131, PD: 318, Prodromal: 110) via anatomically constrained 3D augmentations (rotation ±10°, intensity shifts, Gaussian noise) and Focal Loss optimization (γ = 2.5), balancing classes to 497 samples each. The model achieves state-of-the-art performance: 92.34% accuracy, 0.984 ROC AUC, and 0.974 PR AUC, surpassing six benchmarks including 3DTransMeD (+4.95% accuracy) and SwinTransformer3D (+18.47% accuracy).

For prodromal detection, the most clinically critical task, it attains 96.85% accuracy, 95.85% precision, 94.61% recall, and 0.994 ROC AUC, demonstrating unprecedented sensitivity to early-stage neurodegeneration. Grad-CAM visualizations reveal clinically interpretable decision-making, with attention maps localized to PD-pathology regions (substantia nigra, p < 0.001 via McNemar, bootstrapping). By reducing diagnostic delays by 12–18 months, this framework identifies neurodegeneration 2–3 years before motor symptom onset, enabling timely intervention.

Keywords: Parkinson's disease; 3D MRI; hybrid CNN-Transformer; early detection; medical AI; deep learning

### Preprints 1

Publicly posted; not peer-reviewed.

- Preprint · not peer-reviewed

#### Catching Theft When the Records Lie: Exposure-Based Attribution of Inventory Shrinkage in Pharmaceutical Warehouses

M. J. Alam and A.-N. Khushbu

Preprint, 28 Sep 2026.

Pharmaceutical warehouses lose a substantial quantity of stock to theft each year, and a large part of that loss is not the work of a single employee but of small groups who coordinate to remove goods and to keep the paperwork consistent with what they have taken. Conventional controls inspect records for inconsistencies, yet this defence fails precisely when it is most needed: when the people who commit the theft are also the people who author the records. This study reframes the task from record auditing to actor attribution. Instead of asking whether a document is internally consistent, it asks which employees are associated with unexplained loss more often than their legitimate access to the warehouse would predict.

To make this question answerable and reproducible where real labelled theft data is unavailable, a calibrated simulation of a medicine warehouse is developed, spanning six zones, hundreds of stock items, and a workforce with distinct operational roles. The simulator produces a fully honest baseline year and a set of corrupted years in which adversaries steal through six distinct mechanisms that range from purely physical removal to record editing, while a sealed ground-truth channel records exactly who took what. Routine inventory counts are converted into supervision through a loss-window labelling procedure under a positive-unlabelled assumption that never asserts an employee's innocence. Attribution then proceeds through two complementary channels: one measures each actor's presence in loss windows relative to that same actor's baseline access, and the other measures peer-relative anomalies in authority actions such as write-offs. The system ranks employees for review and never issues an accusation.

Across simulated scenarios that vary the adversary profile and the theft intensity, the two channels place every true actor at the top of the review list in eight of nine worlds at no false-accusation cost, while a count-based audit alone recovers fewer than one in five record-editing thefts. Repeated trials show solo adversaries recovered reliably and a colluding picker-approver pair as the harder case. Run unchanged on a real transaction dataset of roughly 1.07 million records, the method raises no false alarms on legitimate activity and recovers every injected thief, with the record-editing thief writing off several times the value of any honest peer. Validation on real operational data and a neural extension of the attribution score are identified as the immediate next steps. The simulation framework, the labelling procedure, and the attribution method are described in full so that the results can be reproduced and extended.

Keywords: inventory shrinkage; pharmaceutical supply chain; fraud attribution; insider threat; positive-unlabelled learning; anomaly detection; collusion detection

No publications match these filters.


---
