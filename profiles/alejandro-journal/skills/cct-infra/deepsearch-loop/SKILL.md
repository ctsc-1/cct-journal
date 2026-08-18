---
name: deepsearch-loop
title: DeepSearch pour articles CCT
description: Search engine for CCT articles, SearXNG + Gemini, 3 iters, 60s, cache fallback.
category: cct-infra
tags: [search, deepsearch, searxng, gemini, research, article]
---

# DeepSearch Loop — Recherche pour articles CCT

## Principe
Recherche web via SearXNG (port 8888) + Gemini 3.6 Flash pour analyse, avec hard cap 3 iterations et timeout 60s.

## Fallback
Si SearXNG est inaccessible, appel direct à Gemini 3.6 Flash via Gateway (127.0.0.1:4000).

## Securite
- Hard cap 3 iterations
- Timeout 60s par appel
- Fallback cache si echec

## Usage
Charge automatiquement par le profil alejandro-journal avant chaque generation d'article.
