# Projects

Machine learning and computer vision projects by Al-Nahiyan Khushbu, outside the research papers.

- Emotion recognition
- Image + audio
- Cross-modal attention
- RAF-DB + RAVDESS

## MultimodalTransformerNet

A face can be read two ways: what it looks like, and what it sounds like. Using both settles the hard cases.

Two streams (a CNN–Transformer that reads the face, a deep network that reads the voice) meet in a cross-modal attention layer that lets each modality weigh the other before deciding.

- 99.7% Accuracy on balanced RAF-DB + RAVDESS
- 0.9996 AUC: near-perfect separation
- +14.7 pts Over the best single-modality model

### One modality isn't always enough

A single frame can be ambiguous. A downturned mouth might be sadness, or concentration, or fatigue. Vision-only models feel this most on subtle emotions: in this setting a visual-only method reaches only 71% on sadness. The voice carries what the face hides, and the reverse. This model uses both.

### A face and a voice, paired

Each example is a face image drawn from RAF-DB and a short speech clip from RAVDESS, labelled with the same emotion. The datasets are balanced so no emotion dominates training.

100 × 100 × 3 image

~3 s speech clip

Both datasets are released to researchers on request and aren't hosted here; the face and waveform are illustrations.

### The face → a CNN–Transformer hybrid

The image passes through convolution blocks that read local detail, then the feature map is cut into patches and run through Transformer blocks that relate parts of the face to each other, the same hybrid design as GlobalFaceNet. The stream ends as one compact visual feature vector.

CNN (local detail) → patches + Transformer (global context) → visual vector

### The voice → 39 numbers → a deep network

The waveform is summarised as a 39-dimensional feature vector: 13 MFCCs (the timbre of the voice), plus spectral and chroma descriptors (brightness and pitch content). A deep fully-connected network turns those into an acoustic feature vector that matches the visual one in size.

13 MFCC · spectral · chroma = 39-D → dense layers → acoustic vector

### Cross-modal attention: each stream weighs the other

Rather than simply gluing the two vectors together, a cross-modal attention layer lets each modality query the other: the visual features ask "which parts of the voice matter here?", and the voice asks the same of the face. When one modality is uncertain, it leans on the other. The attended features are combined and passed to the classifier.

### Image only · audio only · both

Pick an emotion, then switch the inputs on and off. Watch what happens to the model's confidence when the second modality joins. The jump is largest exactly where a single modality struggles, like sadness. (Illustrative outputs that follow the paper's reported pattern, not live inference.)

Predicted: sad · 99% confident

Image-only would be 71% · audio-only 86% → both 99% (+13.0 pts)


---

- Emotion recognition
- CNN + Vision Transformer
- RAF-DB
- TensorFlow / Keras

## GlobalFaceNet

Reading emotion from a single face, by looking at the fine detail and the whole face at once.

A CNN–Transformer hybrid that classifies a face into one of seven emotions. Two pathways: one reads local detail, the other the global layout, then they combine.

- 85.0% Accuracy on RAF-DB
- 3.2 ms Inference per image
- 7 Emotion classes

### Seven emotions from one face

Given a single face image, the model picks one of seven expressions from the RAF-DB dataset. No audio, no video: just the still image.

- surprise
- fear
- disgust
- happy
- sad
- angry
- neutral
RAF-DB is available to researchers on request and isn't hosted here; the faces below are simple drawings, for illustration.

### A 100×100 face

Each face is resized to 100×100 with three colour channels and normalised. Light augmentation (rotation, shifts, horizontal flip) is used during training.

100 × 100 × 3 · normalised to [0, 1]

### Convolutions read local structure

Four convolution blocks grow the channels and shrink the map (32 → 64 → 128 → 512), turning pixels into local features (edges, brows, mouth shapes).

### Local detail + global context, then fused

The feature maps feed two branches. The CNN path pools the deep 512-channel map for fine local detail. The Transformer path cuts the 128-channel map into 2×2 patches, adds positional encodings, and runs two Transformer blocks (8 heads) to capture global spatial relationships. The two are concatenated, then a dense head predicts the emotion.

### Pick an emotion, watch it classify

Choose an expression; the face shows it and the model's seven-way output updates. (Illustrative example outputs: the shape of a confident prediction.)

Predicted: happy


---

- Bachelor's thesis · 2021
- Transfer learning
- VGG-16 · ResNet-18
- PyTorch

## Bird Species Identification

Even expert birders disagree on a species from one photo. Can a network learn to tell them apart from only a handful of examples each?

A transfer-learning image classifier that names a bird's species from a single photo, standing on the shoulders of networks already trained on millions of images, then re-taught on a small bird dataset.

- 20 Bird species (CUB-200 subset)
- 743 Training images · ~37 each
- 2 Backbones compared

### Which bird is this?

A Blue Jay and an Indigo Bunting are both blue; a House Sparrow and a dozen look-alikes blur together in the field. Telling species apart from a single still photo is a fine-grained recognition problem: the differences are a wing-bar here, an eye-ring there. This project learns to separate 20 species drawn from the Caltech-UCSD Birds collection, the standard benchmark for exactly this kind of task.

- Blue Jay
- Northern Cardinal
- American Goldfinch
- Indigo Bunting
- House Sparrow
- American Robin
The real dataset is photographs; these six are hand-drawn, field-guide style, for illustration. Hover and they preen a little.

### A deliberately small dataset

The full CUB-200-2011 set is 11,788 photos across 200 species, each annotated with a bounding box. This project worked small on purpose: a 20-species slice: just 743 training and 372 validation images, roughly 37 photos per species. Every image is cropped to the bird's bounding box so the background can't leak in, resized to 224 × 224, and normalised to the range the pretrained network expects. With this little data, a deep network trained from scratch would simply memorise the training set, the "curse of dimensionality."

### Borrow a network that already knows how to see

A network starting from zero on 743 images has nothing to go on. So the project borrows one that has already studied 1.2 million: a backbone pre-trained on ImageNet's 1,000 everyday categories. Its early layers already detect edges, textures and feather-like patterns, vision that transfers straight to birds. Those layers are frozen (their weights locked), and only a fresh final classifier (mapping features to the 20 species) is trained. Far fewer parameters to fit means it converges in a handful of epochs instead of overfitting the tiny set.

Only the small head learns at first: a few thousand new weights instead of millions.

### VGG-16 or ResNet-18?

Two backbones were tried head-to-head. VGG-16 (the vgg16_bn variant, with batch-norm) is a tall, plain stack: 13 convolution layers in five blocks, then large fully-connected layers. ResNet-18 is shallower but smarter: its residual skip connections add each block's input back to its output, so gradients flow straight through and the network trains cleanly. In both, only the final layer is swapped for a clean Linear → 20.

### Freeze, then fine-tune

Training ran in two phases. Phase 1 freezes the backbone and trains only the head for 20 epochs at learning rate 0.001; loss drops from 5.3 to under 1 almost at once, and accuracy leaps from 0.5% (random guessing over 20 classes) to 79.9% after a single epoch. Phase 2 unfreezes everything and fine-tunes the whole network for 20 more epochs at a gentler 0.0001, letting the backbone adapt to birds. Adam optimiser, weight decay 1e-6, gradient clipping at 1.0, on a one-cycle schedule.

The actual logged loss from the thesis: training loss falls 1.58 → 0.06, while validation loss flattens near 0.5.

Training accuracy climbs toward 100% while validation plateaus in the low-80s. That gap is the small-data ceiling, honestly reported.

### A top-5 score sheet

For a given photo the model scores every species; the five highest make the score sheet, and the top one is the answer. Pick a bird to see an example, and notice the model's doubt lands on genuinely similar species (the two blues, the streaky browns). (Illustrative outputs in the shape of a confident prediction, not live inference.)

Top match: Northern Cardinal

- Samples per species were uneven, so the model favoured some birds over others.
- Accuracy drops at awkward camera angles and with lower-quality photos, a finding noted in the thesis.
- With only ~37 images per species, the low-80s validation accuracy is a small-data result, not a production one.
- Next step the thesis proposes: a phone app (iOS/Android) and camera-trap deployment for wildlife monitoring.


---

- Computer vision · 2021
- CNN classifier
- OpenCV
- Real-time

## COVID-19 Facemask Detection

At a busy entrance, who's wearing a mask and who isn't? A camera that answers that, face by face, many times a second.

A real-time computer-vision system that finds every face in a video frame and classifies each one as wearing a mask or not, built for public-health compliance monitoring during the pandemic.

- 2 classes Mask / No-mask
- Real-time Per-frame video
- CNN + OpenCV Detect → classify

### Checking masks by eye doesn't scale

During COVID-19, entrances to shops, offices and transit had to verify that people wore masks. A guard can watch one doorway; they cannot watch a hundred, or a crowd, without missing people. The goal here is to automate that check: point a camera at the scene and have software flag, in real time, every uncovered face so staff can act.

### Two stages: find the faces, then judge each one

The system never asks "is there a mask in this picture?" all at once. It runs two stages per frame: first a face detector locates every face and returns a box around it; then a CNN classifier looks at each cropped face on its own and decides mask or no-mask. Finally the result is drawn back onto the frame as a coloured box.

This loop repeats on every frame, so the labels track people as they move through the scene.

### First, where are the faces?

Before anything can be classified, the system has to locate faces in the frame. A classical OpenCV face detector scans the image at many positions and scales and returns a bounding box for each face it finds, regardless of whether that face is masked. Each box is then cropped out and passed on alone, so the classifier sees one clean face at a time instead of the whole busy scene.

The detector only answers "is this a face, and where?" It says nothing yet about masks.

### A real face detector, live on this page

The diagrams above are illustrations. This one is not: it runs BlazeFace, a small pre-trained face detector from Google, inside your browser with TensorFlow.js, the same job as the project's first stage, finding every face and boxing it. It is a stand-in: the project's own trained models were lost, so faces are found but not classified as masked or unmasked here.

The model (about 0.5 MB) loads only when you press Run.

Use your own camera instead? Video stays on your device: frames are processed in this tab and nothing is uploaded or stored. The camera turns off when you press Stop or leave the page.

Boxes and scores are the detector's own output on that image: a demonstration of the method, not a measured accuracy. Small, turned or partly hidden faces can be missed. Sample photos: U.S. Army, NASA and U.S. Navy, public domain (credits).

### A CNN decides: mask or no-mask

Each cropped face is resized to a fixed size and normalised, then passed through a convolutional neural network. Early layers pick up edges and texture; deeper layers respond to the tell-tale pattern of a mask: the straight top edge across the bridge of the nose, the pleats, the covered mouth. A final layer collapses all of that into two numbers: the confidence that the face is masked, and that it is not.

Two-class output (softmax): toggle the mask and watch the decision flip. Faces are drawings, not photographs; confidences illustrative.

### Real-time, face by face

Put it together and it runs live on video. Hit Live feed and watch it keep scanning as people change, or toggle masks yourself and Scan once: the detector sweeps the frame, boxes each face, and the classifier colours it green (mask) or red (no-mask) with a confidence. On real video this repeats every frame, so the overlay follows people as they move.

Click any face to toggle its mask; it re-scans instantly. Or hit Live feed to let it run, people changing as they pass. Confidence values are illustrative, in the shape of a real prediction.

### From "is there a mask?" to "who is this?"

A mask hides the mouth and nose, but not the eyes. The natural next step is to read the eye and upper-face (periocular) region, which stays visible even when someone is masked. That opens up two directions beyond compliance monitoring:

These are directions this work points toward: recognising a person from the eyes when a mask covers the rest.


---

- Internship · 2021
- Recommender systems
- Collaborative + content
- Python · Flask · SQL

## E-Commerce Recommendation Engine

A store with a million products, and one shopper. Which handful do you put in front of them, right now, as they browse?

A hybrid recommender built during an internship: it blends what a shopper has bought before with what they're doing this very session, to personalise the storefront in real time.

- Hybrid Collaborative + content-based
- Real-time Session-aware ranking
- Two-stage Retrieve → rank

### Too many products, too little attention

A large e-commerce catalogue holds hundreds of thousands of items; a shopper will look at a dozen. Showing everyone the same best-sellers wastes that attention. The job of a recommender is to put the few most relevant items in front of each person, and to update that choice as they move through the site, click by click.

### Who you are, and what you're doing now

Good recommendations draw on two very different signals. One is long-term history: everything you've bought and rated before, which says who you are as a shopper. The other is the live session: the items you've clicked, searched and viewed in the last few minutes, which says what you want today. This engine uses both, and weighs the fresh signal heavily.

### "People like you also bought…"

Collaborative filtering never looks at what a product is. It learns from behaviour: every shopper and every item is turned into a vector (a learned embedding, from matrix factorisation over the purchase matrix), placed so that people who buy alike, and items bought together, sit close. To recommend, it simply reads off the nearest items. Click a product to see its neighbours in that learned space.

Distance = learned similarity. The clusters emerge from behaviour alone; no one labelled them.

### …and "more like what you looked at"

Collaborative filtering has a blind spot: a brand-new item nobody has bought yet has no behaviour to learn from (the cold-start problem). Content-based filtering fills it. Each item carries a feature vector (category, brand, price band, specs, text and image features), and the engine recommends items whose features match what you engaged with. It needs no purchase history, so it works from a shopper's very first click.

Content-based covers new items and new shoppers; collaborative captures taste that features can't describe. Together they cover each other's gaps.

### Two stages: retrieve a few hundred, then rank them

Scoring a million items for every page view is impossible in the milliseconds a page has. So production recommenders split the work. Candidate generation cheaply retrieves a few hundred plausible items from collaborative neighbours, content matches and what's trending. Then a heavier ranking model scores just those, folding in live session features, and returns the top handful. Served behind a Flask API over a SQL store.

Flask serves the request; SQL holds the catalogue, interactions and precomputed embeddings.

### Pick a shopper, then browse

Choose a shopper profile to set the long-term history, then click products to "view" them and watch the recommendation rail re-rank in real time. Each pick shows why it's there: collaborative, content, your profile, or trending. This is the hybrid, two-stage idea in miniature.

Scores and the blend are illustrative: a faithful sketch of how a hybrid ranker behaves, not the production model.


---

- Medical imaging
- Deep learning
- CNN · Grad-CAM
- X-ray

## Bone Fracture Detection in X-rays

A hairline crack on an X-ray is easy to miss on a busy shift. Can a model catch it, and point to exactly where?

A deep-learning pipeline that reads a radiograph, decides whether a bone is fractured, and highlights the region that drove its decision, built to support a clinician's read, not replace it.

This is an assistive concept: it highlights potential fractures for a clinician to review. It is not a diagnostic device, and every flag needs expert confirmation.

- Classify Fracture / normal
- Localise Grad-CAM heatmap
- Assistive Flags for clinician review

### Radiographs from across the body, labelled fracture or normal

Training starts from X-ray images of many regions (wrist, ankle, knee, hand), each labelled by radiologists as normal or fractured, often with the fracture's location marked. Real fracture datasets are imbalanced and subtle: most films are normal, and a break can be a single faint line.

Wrist · Normal

Ankle · Fracture

Knee · Normal

Hand · Fracture

Synthetic radiographs generated for this page; no patient images are used. Two of the four carry a fracture.

### What a fracture looks like to a pixel

An X-ray is a grayscale map of density: dense bone stops more radiation and shows bright, soft tissue shows dark. A fracture breaks that smooth bright cortex: a thin dark lucent line, a step in the bone's edge, or a change in texture. Here, a transverse break crosses the distal radius at the wrist, the most common fracture site of all.

The arrow marks the break across the distal radius.

### Make the bone edges speak

Raw films are noisy and unevenly exposed, so each image is cleaned before the model sees it. Contrast enhancement (CLAHE) lifts faint structure out of flat regions; a smoothing filter suppresses grain; and edge detection sharpens the cortical outlines where fractures hide. The result is resized, normalised, and augmented (flips, small rotations) to enlarge the dataset.

The original film: faint, grainy, unevenly lit.

### A CNN reads structure, coarse to fine

The prepared image flows through a deep convolutional network. Early layers fire on edges and gradients; middle layers assemble them into bone outlines and joint spaces; deep layers respond to the abnormal patterns of a break: a discontinuity in the cortex, an unexpected lucency. Each block shrinks the map and grows the channels, distilling the film into features a classifier can judge.

conv → pool, repeated: local detail becomes abstract "fracture-ness."

### Grad-CAM: not just "yes", but "here"

A yes/no answer isn't enough for a clinician; they need to know where. Grad-CAM traces the "fracture" decision back through the final convolutional layer to produce a heatmap over the image, warm where the evidence is strongest. Overlaid on the film, it turns a label into a pointer, drawing the eye straight to the suspected break for confirmation.

Illustrative heatmap. A true Grad-CAM map is computed from the model's own gradients, not drawn.

### Reading the output, and choosing how cautious to be

The classifier returns a fracture probability. Turning that into a flag means picking a threshold, and that choice is a trade-off: lower it and you catch more fractures (high sensitivity) at the cost of more false alarms (lower specificity). For a triage tool meant to catch missed breaks, you lean toward sensitivity and let the clinician rule out the extras. Drag the threshold to feel the trade-off.

Illustrative operating curve: the shape of the sensitivity/specificity trade-off, not measured values. The p(fracture) of 0.72 is an example output, also illustrative.

However it's tuned, the output is a flag for a radiologist, never a final diagnosis.


---
