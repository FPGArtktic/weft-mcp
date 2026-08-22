# SPDX-License-Identifier: GPL-3.0-only
"""Turning text into vectors with BGE-M3.

ONNX Runtime rather than a training framework: it runs the exported model and
nothing else, which is all retrieval needs. See PROJECT.md 5.3 for why not
FlagEmbedding, which is equally MIT but arrives with torch behind it.

The model is not shipped and not downloaded at runtime; it is whatever the
configuration points at. Nothing here reaches the network.
"""

from dataclasses import dataclass
from pathlib import Path

#: BGE-M3's dense embedding is the first token of the last hidden state,
#: normalised. Its width, and the width sqlite-vec is told to expect.
DIMENSIONS = 1024

#: The model's own limit. Chunks are far shorter, but a caller may not know.
MAX_TOKENS = 8192

#: Batches are padded to their longest member, which is why sorting by length
#: matters more than the number here. Measured over IEEE 1800-2017's 2850
#: chunks: unsorted, a batch of 32 computes 1.67x the real tokens; sorted, it
#: computes 1.01x.
DEFAULT_BATCH = 16


class EmbedError(RuntimeError):
    """The model cannot be loaded or run."""


@dataclass(frozen=True)
class ModelFiles:
    """Where the pieces of an exported model live.

    @graph: the .onnx file; external weights sit beside it under the name the
            graph records, so both have to stay in the same directory
    @tokenizer: tokenizer.json, the vocabulary and merge rules
    """

    graph: Path
    tokenizer: Path

    @classmethod
    def under(cls, directory: Path) -> "ModelFiles":
        """under - the usual layout of an exported BGE-M3

        Accepts either the repository root, where the graph is in onnx/, or
        that subdirectory itself.
        """
        directory = Path(directory)
        for graph in (directory / "onnx" / "model.onnx", directory / "model.onnx"):
            if graph.is_file():
                break
        else:
            raise EmbedError(f"no model.onnx under {directory}")

        for tokenizer in (
            graph.parent / "tokenizer.json",
            directory / "tokenizer.json",
        ):
            if tokenizer.is_file():
                return cls(graph=graph, tokenizer=tokenizer)
        raise EmbedError(f"no tokenizer.json beside {graph}")


class Embedder:
    """A loaded BGE-M3, ready to encode.

    Loading takes seconds and a couple of gigabytes of memory, so one instance
    is kept and reused rather than built per call.
    """

    def __init__(self, directory: Path, threads: int | None = None):
        """__init__ - load the model and its tokenizer

        @directory: where the exported model lives
        @threads: ONNX Runtime thread count; None leaves its default

        Raises EmbedError if the files are missing or either library is not
        installed.
        """
        files = ModelFiles.under(directory)

        try:
            import onnxruntime
            from tokenizers import Tokenizer
        except ImportError as e:
            raise EmbedError(f"embedding needs onnxruntime and tokenizers: {e}") from e

        options = onnxruntime.SessionOptions()
        if threads:
            options.intra_op_num_threads = threads

        try:
            self._session = onnxruntime.InferenceSession(
                str(files.graph), options, providers=["CPUExecutionProvider"]
            )
            self._tokenizer = Tokenizer.from_file(str(files.tokenizer))
        except Exception as e:  # onnxruntime raises its own hierarchy
            raise EmbedError(f"cannot load the model at {files.graph}: {e}") from e

        self._tokenizer.enable_truncation(max_length=MAX_TOKENS)
        self._tokenizer.enable_padding()
        self._inputs = {i.name for i in self._session.get_inputs()}

    @property
    def dimensions(self) -> int:
        """dimensions - width of the vectors this produces."""
        return DIMENSIONS

    def encode(self, texts: list[str], batch: int = DEFAULT_BATCH) -> list[list[float]]:
        """encode - one vector per text

        @texts: what to embed, in order
        @batch: how many to run through the model at once

        Vectors are normalised, so a dot product is a cosine similarity and
        sqlite-vec's L2 distance orders the same way.

        Texts are batched by length rather than in the order given. A batch is
        padded to its longest member, so mixing a one-line chunk with a full
        clause makes the model compute mostly padding. Over IEEE 1800-2017 that
        waste is a third of the work at a batch of 16 and rises with batch
        size; sorting removes it, and the results are put back in the caller's
        order before returning.

        Return: one vector of DIMENSIONS floats per text, in order.

        Raises EmbedError if the model rejects the input.
        """
        if not texts:
            return []

        import numpy as np

        width = max(batch, 1)
        order = sorted(range(len(texts)), key=lambda i: len(texts[i]))
        vectors: list[list[float] | None] = [None] * len(texts)

        for start in range(0, len(order), width):
            positions = order[start : start + width]
            window = [texts[i] for i in positions]
            encoded = self._tokenizer.encode_batch(window)

            feed = {"input_ids": np.array([e.ids for e in encoded], dtype=np.int64)}
            if "attention_mask" in self._inputs:
                feed["attention_mask"] = np.array(
                    [e.attention_mask for e in encoded], dtype=np.int64
                )
            if "token_type_ids" in self._inputs:
                feed["token_type_ids"] = np.zeros_like(feed["input_ids"])

            try:
                hidden = self._session.run(None, feed)[0]
            except Exception as e:
                raise EmbedError(f"the model rejected the batch: {e}") from e

            # BGE-M3's dense vector is the first token, normalised.
            batched = np.asarray(hidden)[:, 0, :].astype(np.float32)
            norms = np.linalg.norm(batched, axis=1, keepdims=True)
            batched = batched / np.maximum(norms, 1e-12)
            for position, vector in zip(positions, batched, strict=True):
                vectors[position] = vector.tolist()

        return [v for v in vectors if v is not None]
