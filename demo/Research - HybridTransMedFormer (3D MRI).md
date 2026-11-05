# Research - HybridTransMedFormer (3D MRI)


- Under review
- Artificial Intelligence in Medicine
- 3D MRI
- Thesis

## HybridTransMedFormer: a dual-path 3D MRI framework for early Parkinson's detection

A.-N. Khushbu, X. Zhang, A. K. M. M. Islam, Y. Liu, and Y. Pan · Artificial Intelligence in Medicine · 2026

1 min read

Early Parkinson’s is hard to catch because the clearest signs appear only after irreversible damage. HybridTransMedFormer reads a single 3D T1-weighted MRI and combines local convolutional features with global self-attention to separate healthy, prodromal, and Parkinson’s brains, reaching the prodromal stage 2–3 years before motor symptoms.

### What’s new

- One scan, no extra modalities. The model works from structural MRI alone, without the multi-modal inputs most early-stage approaches depend on.
- Two paths, fused dynamically. A CNN path captures fine local structure such as substantia nigra atrophy; a Transformer path captures cortical–subcortical dependencies across the whole brain. Cross-pathway fusion lets each path weight the other instead of simply concatenating them.
- Built for an imbalanced cohort. Trained on the PPMI dataset with anatomically constrained 3D augmentation and focal loss to cope with uneven class sizes.
- Interpretable. Grad-CAM maps show the model attending to regions associated with Parkinson’s pathology.
This work forms the 3D, confirmatory half of my Master’s thesis; the 2D pre-screening model is the other half.

- 92.34% accuracy
- 0.984 ROC AUC
- 96.85% prodromal acc.
- 0.994 prodromal AUC


---
