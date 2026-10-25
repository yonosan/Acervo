from collections import Counter
from pathlib import Path
import re
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from i18n import CATALOG, translate


class TranslationTests(unittest.TestCase):
    def test_english_default_and_portuguese(self):
        self.assertEqual(translate('Configurações'), 'Settings')
        self.assertEqual(translate('Abrir Acervo'), 'Open Acervo')
        self.assertEqual(translate('Configurações', 'pt-BR'), 'Configurações')

    def test_variables_keep_original_names_and_symbols(self):
        title = 'Erro & Música <ao vivo>'
        self.assertEqual(translate('Capa de ' + title), 'Artwork for ' + title)
        self.assertEqual(translate('Baixar MP3 de ' + title), 'Download MP3 for ' + title)
        self.assertEqual(translate('Unknown provider response'), 'Unknown provider response')

    def test_patterns_keep_all_placeholders(self):
        for source, target in CATALOG['patterns'].items():
            self.assertEqual(Counter(re.findall(r'\{\d+\}', source)), Counter(re.findall(r'\{\d+\}', target)), source)

    def test_combined_matching_reasons_and_empty_optional_parts(self):
        self.assertEqual(translate('Título, artista e duração compatíveis. Álbum não informado pela fonte.'),
                         'Title, artist and duration match. The source did not provide an album.')
        self.assertEqual(translate('3 curtidas lidas da página. '), '3 liked songs read from the page. ')


if __name__ == '__main__':
    unittest.main()
