"""
Pipeline for CONLL-U formatting.
"""
# pylint: disable=too-few-public-methods, unused-import, undefined-variable, too-many-nested-blocks
import pathlib

import spacy_udpipe
import stanza
from stanza.models.common.doc import Document
from stanza.pipeline.core import Pipeline
from stanza.utils.conll import CoNLL

from core_utils.article.article import (Article, ArtifactType, get_article_id_from_filepath,
                                        split_by_sentence)
from core_utils.article.io import from_meta, from_raw, to_cleaned, to_meta
from core_utils.constants import ASSETS_PATH, UDPIPE_MODEL_PATH
from core_utils.pipeline import (AbstractCoNLLUAnalyzer, CoNLLUDocument, LibraryWrapper,
                                 PipelineProtocol, StanzaDocument, TreeNode)
from core_utils.visualizer import visualize

try:
    from networkx import DiGraph
except ImportError:  # pragma: no cover
    DiGraph = None  # type: ignore
    print('No libraries installed. Failed to import.')


class EmptyDirectoryError(Exception):
    """
    Directory is empty.
    """


class InconsistentDatasetError(Exception):
    """
    IDs contain slips and/or number of meta and raw files is not equal and/or files are empty.
    """


class CorpusManager:
    """
    Work with articles and store them.
    """

    def __init__(self, path_to_raw_txt_data: pathlib.Path) -> None:
        """
        Initialize an instance of the CorpusManager class.

        Args:
            path_to_raw_txt_data (pathlib.Path): Path to raw txt data
        """
        self.path_to_raw_txt_data = path_to_raw_txt_data
        self._storage = {}
        self._validate_dataset()
        self._scan_dataset()

    def _validate_dataset(self) -> None:
        """
        Validate folder with assets.
        """
        if not self.path_to_raw_txt_data.exists():
            raise FileNotFoundError

        if not self.path_to_raw_txt_data.is_dir():
            raise NotADirectoryError

        if not any(self.path_to_raw_txt_data.iterdir()):
            raise EmptyDirectoryError

        meta_files = list(self.path_to_raw_txt_data.glob('*_meta.json'))
        raw_files = list(self.path_to_raw_txt_data.glob('*_raw.txt'))
        if len(meta_files) != len(raw_files):
            raise InconsistentDatasetError

        meta_files.sort(key=lambda x: int(get_article_id_from_filepath(x)))
        raw_files.sort(key=lambda x: int(get_article_id_from_filepath(x)))

        for index, (meta, raw) in enumerate(zip(meta_files, raw_files)):
            if index + 1 != get_article_id_from_filepath(meta) \
                    or index + 1 != get_article_id_from_filepath(raw) \
                    or meta.stat().st_size == 0 or raw.stat().st_size == 0:
                raise InconsistentDatasetError

    def _scan_dataset(self) -> None:
        """
        Register each dataset entry.
        """
        for file in self.path_to_raw_txt_data.glob('*_raw.txt'):
            article_id = get_article_id_from_filepath(file)
            self._storage[article_id] = from_raw(
                path=file,
                article=Article(url=None,
                                article_id=get_article_id_from_filepath(file))
            )

    def get_articles(self) -> dict:
        """
        Get storage params.

        Returns:
            dict: Storage params
        """
        return self._storage


class TextProcessingPipeline(PipelineProtocol):
    """
    Preprocess and morphologically annotate sentences into the CONLL-U format.
    """

    def __init__(
            self, corpus_manager: CorpusManager, analyzer: LibraryWrapper | None = None
    ) -> None:
        """
        Initialize an instance of the TextProcessingPipeline class.

        Args:
            corpus_manager (CorpusManager): CorpusManager instance
            analyzer (LibraryWrapper | None): Analyzer instance
        """
        self._corpus = corpus_manager
        self._analyzer = analyzer

    def run(self) -> None:
        """
        Perform basic preprocessing and write processed text to files.
        """
        for article in self._corpus.get_articles().values():
            to_cleaned(article)
            if self._analyzer:
                text_list = split_by_sentence(article.text)
                text_analyzed = self._analyzer.analyze(text_list)
                article.set_conllu_info(text_analyzed)
                self._analyzer.to_conllu(article)


class UDPipeAnalyzer(LibraryWrapper):
    """
    Wrapper for udpipe library.
    """

    _analyzer: AbstractCoNLLUAnalyzer

    def __init__(self) -> None:
        """
        Initialize an instance of the UDPipeAnalyzer class.
        """
        self._analyzer = self._bootstrap()

    def _bootstrap(self) -> AbstractCoNLLUAnalyzer:
        """
        Load and set up the UDPipe model.

        Returns:
            AbstractCoNLLUAnalyzer: Analyzer instance
        """
        model = spacy_udpipe.load_from_path(lang="ru",
                                            path=str(UDPIPE_MODEL_PATH)
                                            )
        model.add_pipe("conll_formatter",
                       last=True,
                       config={"conversion_maps": {"XPOS": {"": "_"}}, "include_headers": True},
                       )
        return model

    def analyze(self, texts: list[str]) -> list[StanzaDocument | str]:
        """
        Process texts into CoNLL-U formatted markup.

        Args:
            texts (list[str]): Collection of texts

        Returns:
            list[StanzaDocument | str]: List of documents
        """
        return [f"{self._analyzer(text)._.conll_str}\n" for text in texts]

    def to_conllu(self, article: Article) -> None:
        """
        Save content to ConLLU format.

        Args:
            article (Article): Article containing information to save
        """
        with open(article.get_file_path(
                kind=ArtifactType.UDPIPE_CONLLU), 'w', encoding='utf-8') as annotation_file:
            annotation_file.writelines(article.get_conllu_info())


class StanzaAnalyzer(LibraryWrapper):
    """
    Wrapper for stanza library.
    """

    _analyzer: AbstractCoNLLUAnalyzer

    def __init__(self) -> None:
        """
        Initialize an instance of the StanzaAnalyzer class.
        """
        self._analyzer = self._bootstrap()

    def _bootstrap(self) -> AbstractCoNLLUAnalyzer:
        """
        Load and set up the Stanza model.

        Returns:
            AbstractCoNLLUAnalyzer: Analyzer instance
        """
        language = "ru"
        processors = "tokenize,pos,lemma,depparse"
        stanza.download(lang=language, processors=processors, logging_level="INFO")
        model = Pipeline(
            lang=language,
            processors=processors,
            logging_level="INFO",
            download_method=None
        )
        return model

    def analyze(self, texts: list[str]) -> list[StanzaDocument]:
        """
        Process texts into CoNLL-U formatted markup.

        Args:
            texts (list[str]): Collection of texts

        Returns:
            list[StanzaDocument]: List of documents
        """
        text = "".join(texts)
        return self._analyzer.process([Document([], text=text)])

    def to_conllu(self, article: Article) -> None:
        """
        Save content to ConLLU format.

        Args:
            article (Article): Article containing information to save
        """
        CoNLL.write_doc2conll(
            doc=article.get_conllu_info()[0],
            filename=article.get_file_path(kind=ArtifactType.STANZA_CONLLU),
        )

    def from_conllu(self, article: Article) -> CoNLLUDocument:
        """
        Load ConLLU content from article stored on disk.

        Args:
            article (Article): Article to load

        Returns:
            CoNLLUDocument: Document ready for parsing
        """
        return CoNLL.conll2doc(input_file=article.get_file_path(kind=ArtifactType.STANZA_CONLLU))


class EmptyFileError(Exception):
    """
    An article file is empty.
    """


class POSFrequencyPipeline:
    """
    Count frequencies of each POS in articles, update meta info and produce graphic report.
    """

    def __init__(self, corpus_manager: CorpusManager, analyzer: LibraryWrapper) -> None:
        """
        Initialize an instance of the POSFrequencyPipeline class.

        Args:
            corpus_manager (CorpusManager): CorpusManager instance
            analyzer (LibraryWrapper): Analyzer instance
        """
        self._corpus = corpus_manager
        self._analyzer = analyzer

    def run(self) -> None:
        """
        Visualize the frequencies of each part of speech.
        """
        for article in self._corpus.get_articles().values():
            if article.get_file_path(kind=ArtifactType.STANZA_CONLLU).stat().st_size == 0:
                raise EmptyFileError

            from_meta(path=article.get_meta_file_path(), article=article)
            pos_freq = self._count_frequencies(article)
            article.set_pos_info(pos_freq)
            to_meta(article=article)

            visualize(article,
                      self._corpus.path_to_raw_txt_data / f'{article.article_id}_image.png')

    def _count_frequencies(self, article: Article) -> dict[str, int]:
        """
        Count POS frequency in Article.

        Args:
            article (Article): Article instance

        Returns:
            dict[str, int]: POS frequencies
        """
        pos_dict = {}
        for pos_tag in self._analyzer.from_conllu(article).get('upos'):
            pos_dict[pos_tag] = pos_dict.get(pos_tag, 0) + 1
        return pos_dict


class PatternSearchPipeline(PipelineProtocol):
    """
    Search for the required syntactic pattern.
    """

    def __init__(
            self, corpus_manager: CorpusManager, analyzer: LibraryWrapper, pos: tuple[str, ...]
    ) -> None:
        """
        Initialize an instance of the PatternSearchPipeline class.

        Args:
            corpus_manager (CorpusManager): CorpusManager instance
            analyzer (LibraryWrapper): Analyzer instance
            pos (tuple[str, ...]): Root, Dependency, Child part of speech
        """

    def _make_graphs(self, doc: CoNLLUDocument) -> list[DiGraph]:
        """
        Make graphs for a document.

        Args:
            doc (CoNLLUDocument): Document for patterns searching

        Returns:
            list[DiGraph]: Graphs for the sentences in the document
        """

    def _add_children(
            self, graph: DiGraph, subgraph_to_graph: dict, node_id: int, tree_node: TreeNode
    ) -> None:
        """
        Add children to TreeNode.

        Args:
            graph (DiGraph): Sentence graph to search for a pattern
            subgraph_to_graph (dict): Matched subgraph
            node_id (int): ID of root node of the match
            tree_node (TreeNode): Root node of the match
        """

    def _find_pattern(self, doc_graphs: list) -> dict[int, list[TreeNode]]:
        """
        Search for the required pattern.

        Args:
            doc_graphs (list): A list of graphs for the document

        Returns:
            dict[int, list[TreeNode]]: A dictionary with pattern matches
        """

    def run(self) -> None:
        """
        Search for a pattern in documents and writes found information to JSON file.
        """


def main() -> None:
    """
    Entrypoint for pipeline module.
    """
    corpus_manager = CorpusManager(path_to_raw_txt_data=ASSETS_PATH)

    # spacy udpipe analyzer
    pipeline_udpipe = TextProcessingPipeline(corpus_manager, UDPipeAnalyzer())
    pipeline_udpipe.run()

    # stanza analyzer
    pipeline_stanza = TextProcessingPipeline(corpus_manager, StanzaAnalyzer())
    pipeline_stanza.run()

    # pos frequency data
    pos_pipeline = POSFrequencyPipeline(corpus_manager, StanzaAnalyzer())
    pos_pipeline.run()


if __name__ == "__main__":
    main()
