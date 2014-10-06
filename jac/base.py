# -*- coding: utf-8 -*-

import os
import hashlib
from bs4 import BeautifulSoup

from jac.compat import u, open, file, basestring, utf8_encode
from jac.compilers import compile
from jac.config import Config
from jac.exceptions import TemplateSyntaxError, TemplateDoesNotExist, OfflineGenerationError
from jac.parser import Jinja2Parser

try:
    from collections import OrderedDict # Python >= 2.7
except ImportError:
    from ordereddict import OrderedDict # Python 2.6


class Compressor(object):

    def __init__(self, **kwargs):
        if 'environment' in kwargs:
            configs = self.get_configs_from_environment(kwargs['environment'])
            self.config = Config(**configs)
        else:
            self.config = Config(**kwargs)

    def compress(self, html, compression_type):

        if not self.config.compressor_enabled:
            return html

        compression_type = compression_type.lower()
        html_hash = self.make_hash(html)

        if not os.path.exists(u(self.config.compressor_output_dir)):
            os.makedirs(u(self.config.compressor_output_dir))

        cached_file = os.path.join(
            u(self.config.compressor_output_dir),
            u('{hash}.{extension}').format(
                hash=html_hash,
                extension=compression_type,
            ),
        )

        if os.path.exists(cached_file):
            filename = os.path.join(
                u(self.config.compressor_static_prefix),
                os.path.basename(cached_file),
            )
            return self.render_element(filename, compression_type)

        assets = OrderedDict()
        soup = BeautifulSoup(html)
        for count, c in enumerate(self.find_compilable_tags(soup)):
            if c.get('type') is None:
                raise RuntimeError('Tags to be compressed must have a type attribute.')

            url = c.get('src') or c.get('href')
            if url:
                filename = os.path.basename(u(url)).split('.', 1)[0]
                uri_cwd = os.path.join(u(self.config.compressor_static_prefix), os.path.dirname(u(url)))
                text = open(self.find_file(u(url)), 'r', encoding='utf-8')
                cwd = os.path.dirname(text.name)
            else:
                filename = u('inline{0}').format(count)
                uri_cwd = None
                text = c.string
                cwd = None

            must_compile = c['type'] in ['text/less', 'text/sass', 'text/scss']
            if not self.config.compressor_debug or must_compile:
                compressed = compile(self.get_contents(text), c['type'], cwd=cwd,
                               uri_cwd=uri_cwd, debug=self.config.compressor_debug)
            else:
                compressed = self.get_contents(text)

            if not self.config.compressor_debug:
                outfile = cached_file
            else:
                outfile = os.path.join(
                    u(self.config.compressor_output_dir),
                    u('{hash}-{filename}.{extension}').format(
                        hash=html_hash,
                        filename=filename,
                        extension=compression_type,
                    ),
                )

            if assets.get(outfile) is None:
                assets[outfile] = u('')
            assets[outfile] += u("\n") + compressed

        blocks = u('')
        for outfile, asset in assets.items():
            with open(outfile, 'w', encoding='utf-8') as fh:
                fh.write(asset)
            filename = os.path.join(
                u(self.config.compressor_static_prefix),
                os.path.basename(outfile),
            )
            blocks += self.render_element(filename, compression_type)

        return blocks

    def make_hash(self, html):
        soup = BeautifulSoup(html)
        compilables = self.find_compilable_tags(soup)
        html_hash = hashlib.md5(utf8_encode(html))

        for c in compilables:
            url = c.get('src') or c.get('href')
            if url:
                stat = os.stat(self.find_file(u(url)))
                html_hash.update(utf8_encode('{0}-{1}'.format(stat.st_size, stat.st_mtime)))

        return html_hash.hexdigest()

    def find_file(self, path):
        if callable(self.config.compressor_source_dirs):
            filename = self.config.compressor_source_dirs(path)
            if os.path.exists(filename):
                return filename
        else:
            if isinstance(self.config.compressor_source_dirs, basestring):
                dirs = [self.config.compressor_source_dirs]
            else:
                dirs = self.config.compressor_source_dirs

            for d in dirs:
                filename = os.path.join(d, path.lstrip(os.sep).lstrip('/'))
                if os.path.exists(filename):
                    return filename

        raise IOError(2, u('File not found {0}').format(path))

    def find_compilable_tags(self, soup):
        tags = ['link', 'style', 'script']
        for tag in soup.find_all(tags):
            if tag.get('type') is None:
                if tag.name == 'script':
                    tag['type'] = 'text/javascript'
                if tag.name == 'style':
                    tag['type'] = 'text/css'
            else:
                tag['type'] == tag['type'].lower()
            yield tag

    def get_contents(self, src):
        if isinstance(src, file):
            return u(src.read())
        else:
            return u(src)

    def render_element(self, filename, type):
        """Returns an html element pointing to filename as a string.
        """
        if type.lower() == 'css':
            return u('<link type="text/css" rel="stylesheet" href="{0}" />').format(filename)
        elif type.lower() == 'js':
            return u('<script type="text/javascript" src="{0}"></script>').format(filename)
        else:
            raise RuntimeError(u('Unsupported type of compression {0}').format(type))

    def get_configs_from_environment(self, environment):
        configs = {}
        for key in dir(environment):
            if key.startswith('compressor_'):
                configs[key] = getattr(environment, key)
        return configs

    def offline_compress(self, environment):
        compressor_nodes = {}
        parser = Jinja2Parser(charset='utf-8', env=environment)
        for template_path in self.find_template_files():
            try:
                template = parser.parse(template_path)
            except IOError:  # unreadable file -> ignore
                continue
            except TemplateSyntaxError as e:  # broken template -> ignore
                continue
            except TemplateDoesNotExist:  # non existent template -> ignore
                continue
            except UnicodeDecodeError:
                continue

            try:
                nodes = list(parser.walk_nodes(template))
            except (TemplateDoesNotExist, TemplateSyntaxError) as e:
                continue
            if nodes:
                template.template_name = template_path
                compressor_nodes.setdefault(template, []).extend(nodes)

        if not compressor_nodes:
            raise OfflineGenerationError(
                "No 'compress' template tags found in templates. "
                "Try setting follow_symlinks to True")

        for template, nodes in compressor_nodes.items():
            for node in nodes:
                html = parser.render_nodelist({}, node)
                parser.render_node({}, node)

    def find_template_files(self):
        if callable(self.config.compressor_source_dirs):
            source_dirs = self.config.compressor_source_dirs()
        else:
            if isinstance(self.config.compressor_source_dirs, basestring):
                source_dirs = [self.config.compressor_source_dirs]
            else:
                source_dirs = self.config.compressor_source_dirs

        templates = set()

        if not source_dirs:
            raise RuntimeError('Missing compressor_source_dirs.')

        for d in source_dirs:
            for root, dirs, files in os.walk(d,
                    followlinks=self.config.compressor_follow_symlinks):
                templates.update(os.path.join(root, name)
                    for name in files if not name.startswith('.'))
        return templates
