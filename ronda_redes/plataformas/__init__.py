"""Registro das plataformas suportadas pela Ronda Redes."""

from plataformas.base import Plataforma, SessaoExpirada, novo_post
from plataformas.twitter import Twitter

REGISTRO = {
    "twitter": Twitter,
    # "instagram": Instagram,   ← futuras plataformas
    # "facebook":  Facebook,
    # "tiktok":    TikTok,
    # "youtube":   YouTube,
}
