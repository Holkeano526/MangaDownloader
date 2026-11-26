"""Handlers de sitios. El orden de HANDLERS (core/handler.py) importa: el primero cuyo dominio
aparezca en la URL se queda con ella."""
from .tmohentai_app import TMOHentaiAppHandler
from .lectorhentai import LectorHentaiHandler
from .hentainexus import HentaiNexusHandler
from .hitomi import HitomiHandler
from .nhentai_mirrors import NHentaiMirrorHandler
from .h2r import H2RHandler
from .m440 import M440Handler
from .hentalk import HentalkHandler
from .shademanga import ShadeMangaHandler
