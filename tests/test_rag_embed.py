# SPDX-License-Identifier: GPL-3.0-only
"""Tests for the embedding runtime.

The model is 2.3 GB and is not in the repository, so what runs here is
everything around it: how the files are found, what happens when they are not,
and that batching by length returns vectors in the caller's order rather than
the model's. The model itself is exercised by tests/manual/index_pdf.py.
"""

import pytest

from weft.rag.embed import DIMENSIONS, Embedder, EmbedError, ModelFiles


def test_the_usual_export_layout_is_found(tmp_path):
    (tmp_path / "onnx").mkdir()
    (tmp_path / "onnx" / "model.onnx").write_bytes(b"")
    (tmp_path / "tokenizer.json").write_text("{}")
    found = ModelFiles.under(tmp_path)
    assert found.graph == tmp_path / "onnx" / "model.onnx"
    assert found.tokenizer == tmp_path / "tokenizer.json"


def test_the_onnx_subdirectory_works_on_its_own(tmp_path):
    (tmp_path / "model.onnx").write_bytes(b"")
    (tmp_path / "tokenizer.json").write_text("{}")
    assert ModelFiles.under(tmp_path).graph == tmp_path / "model.onnx"


def test_a_missing_model_says_so(tmp_path):
    with pytest.raises(EmbedError, match="no model.onnx"):
        ModelFiles.under(tmp_path)


def test_a_missing_tokenizer_says_so(tmp_path):
    (tmp_path / "model.onnx").write_bytes(b"")
    with pytest.raises(EmbedError, match="no tokenizer.json"):
        ModelFiles.under(tmp_path)


class FakeTokenizer:
    """One id per character, padded to the longest in the batch as the real
    tokenizer does once padding is enabled."""

    @staticmethod
    def encode_batch(texts):
        width = max((len(t) for t in texts), default=0)
        return [
            type(
                "E",
                (),
                {
                    "ids": list(range(len(t))) + [0] * (width - len(t)),
                    "attention_mask": [1] * len(t) + [0] * (width - len(t)),
                },
            )()
            for t in texts
        ]

    def enable_truncation(self, **kwargs):
        pass

    def enable_padding(self, **kwargs):
        pass


class FakeSession:
    """A model whose output encodes each text's length, so a mix-up in
    ordering is visible in the result."""

    def get_inputs(self):
        return [type("I", (), {"name": "input_ids"})()]

    def run(self, _outputs, feed):
        import numpy as np

        ids = feed["input_ids"]
        hidden = np.zeros((len(ids), 1, DIMENSIONS), dtype=np.float32)
        for row, sequence in enumerate(ids):
            # Padding ids are zero, so the count of non-zero ids plus one is
            # the original length: the marker travels with the text.
            # The marker is a ratio, not a magnitude: encode() normalises, and
            # a single component would come back as 1.0 for every text.
            hidden[row, 0, 0] = float(sum(1 for i in sequence if i) + 1)
            hidden[row, 0, 1] = 1.0
        return [hidden]


@pytest.fixture
def embedder(monkeypatch, tmp_path):
    """embedder - an Embedder with the model swapped out."""
    (tmp_path / "model.onnx").write_bytes(b"")
    (tmp_path / "tokenizer.json").write_text("{}")

    made = Embedder.__new__(Embedder)
    made._session = FakeSession()
    made._tokenizer = FakeTokenizer()
    made._inputs = {"input_ids"}
    return made


def test_nothing_in_nothing_out(embedder):
    assert embedder.encode([]) == []


def test_vectors_come_back_in_the_callers_order(embedder):
    """Batching by length reorders the work; the answer must not follow it."""
    texts = ["a" * n for n in (50, 3, 200, 17)]
    got = embedder.encode(texts, batch=2)
    assert [round(v[0] / v[1]) for v in got] == [50, 3, 200, 17]


def test_every_text_gets_a_vector(embedder):
    got = embedder.encode(["x" * n for n in range(1, 20)], batch=4)
    assert len(got) == 19
    assert all(len(v) == DIMENSIONS for v in got)


def test_vectors_are_normalised(embedder):
    """A dot product is then a cosine, and sqlite-vec's L2 distance orders the
    same way."""
    got = embedder.encode(["abc"], batch=1)
    assert abs(sum(x * x for x in got[0]) - 1.0) < 1e-6
