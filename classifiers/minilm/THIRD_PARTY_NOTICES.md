# Source model and changes

The checkpoint is derived from
[sentence-transformers/paraphrase-MiniLM-L3-v2](https://huggingface.co/sentence-transformers/paraphrase-MiniLM-L3-v2),
revision `4ca70771034acceecb2e72475f72050fcdde4ddc`, by the Sentence Transformers
project (Nils Reimers and contributors). The upstream model card declares
Apache License 2.0. A copy is included as [LICENSE-BASE-MODEL](LICENSE-BASE-MODEL).
This notice describes the pretrained model; it does not relicense the whole Actor repository.

Changes: the BERT encoder was fine-tuned and a two-class sequence-classification
head trained for ACTOR strategic review. The latest checkpoint is
`minilm-v7-r2`, dated 2026-10-23 (continued from MiniLM v6). The tokenizer is bundled;
the canonical input renderer and task-specific Runtime projection are included.
This is a task-specific derivative, not an upstream Sentence Transformers release.

Upstream reference: Reimers and Gurevych (2019),
[Sentence-BERT: Sentence Embeddings using Siamese BERT-Networks](https://arxiv.org/abs/1908.10084).
