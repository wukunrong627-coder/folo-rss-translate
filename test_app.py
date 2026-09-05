import unittest
from unittest.mock import patch, MagicMock
from pathlib import Path
import tempfile
import generate
import xml.etree.ElementTree as ET
from defusedxml.common import DefusedXmlException
from app import SafeET, make_feed, plain_text


class FakeTranslator:
    def translate(self, text):
        return "中文：" + text


class FeedSafetyTests(unittest.TestCase):
    def setUp(self):
        self.source = {"id": "test", "title": "测试", "site": "https://example.org", "max_items": 20}

    def test_stable_identity_dates_and_original_text(self):
        channel = ET.fromstring('<channel><item><title>A &amp; B</title><link>https://example.org/news/1</link><guid>original-1</guid><pubDate>Mon, 01 Sep 2025 10:00:00 GMT</pubDate><description>&lt;p&gt;A short report.&lt;/p&gt;</description></item></channel>')
        output, count = make_feed(self.source, channel, FakeTranslator())
        first = ET.fromstring(output).find('channel/item')
        again = ET.fromstring(make_feed(self.source, channel, FakeTranslator())[0]).find('channel/item')
        self.assertEqual(count, 1)
        self.assertEqual(first.findtext('guid'), again.findtext('guid'))
        self.assertEqual(first.findtext('pubDate'), channel.findtext('item/pubDate'))
        self.assertIn('A short report.', first.findtext('description'))
        self.assertIn('中文：', first.findtext('title'))

    def test_script_removal_and_empty_source_failure(self):
        self.assertEqual(plain_text('<script>alert(1)</script><p>News</p>'), 'News')
        with self.assertRaises(ValueError):
            make_feed(self.source, ET.fromstring('<channel/>'), FakeTranslator())

    def test_entity_expansion_is_rejected(self):
        with self.assertRaises(DefusedXmlException):
            SafeET.fromstring('<!DOCTYPE rss [<!ENTITY test SYSTEM "file:///etc/passwd">]><rss>&test;</rss>')

    def test_second_source_failure_preserves_published_feeds(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'ap.xml').write_bytes(b'previous AP feed')
            (root / 'yahoo.xml').write_bytes(b'previous Yahoo feed')
            with patch.dict('os.environ', {'OUTPUT_DIR': directory}), \
                 patch.object(generate, 'DATA', root), \
                 patch.object(generate, 'Translator', return_value=MagicMock()), \
                 patch.object(generate, 'get_feed', side_effect=[object(), RuntimeError('source unavailable')]), \
                 patch.object(generate, 'make_feed', return_value=(b'new AP feed', 1)):
                with self.assertRaises(RuntimeError):
                    generate.main()
            self.assertEqual((root / 'ap.xml').read_bytes(), b'previous AP feed')
            self.assertEqual((root / 'yahoo.xml').read_bytes(), b'previous Yahoo feed')


if __name__ == '__main__':
    unittest.main()

