import elasticsearch
import requests
import logging
from bs4 import BeautifulSoup
from django.shortcuts import render
from django.conf import settings
from django.http import HttpResponse, HttpResponseRedirect
from django.contrib import messages
from .helpers import (data_processor, variant_to_query, ethno_table_maker,
                      query_kreator, get_variants)
from .forms import SearchPostForm
from elasticsearch import Elasticsearch

LOGGER = logging.getLogger(__name__)

# Cliente de `elasticsearch`
es = Elasticsearch([settings.ELASTIC_URL])

# === Búsqueda ===


def search(request):
    """**Realiza la búsqueda y muestra los resultados**

    Vista encargada de construir el archivo ``json`` que será mandado al API
    de ``Elasticsearch`` para ejecutar la *query*. Posteriorimente preprocesa
    la respuesta del API y envía las variables para ser desplegadas en
    el template ``sercher.html``. Además, reenvia el formulario con la
    información previamente introducida para nuevas búsquedas.

    :param request: Objeto ``HttpRequets`` para pasar el estado de la app a
                    través del sistema
    :type: ``HttpRequest``
    :return: Resultados de búsqueda y formulario para nuevas búsquedas
    """
    if request.method == "POST":
        # Pasando información al formulario. Esta será reenviada al
        # template
        form = SearchPostForm(request.POST)
        current_variants = get_variants()
        if current_variants['status'] == 'success':
            del current_variants['status']
            if len(current_variants):
                form.fields['variante'].choices = current_variants.items()
            else:
                form.fields['variante'].widget.attrs['disabled'] = True
        else:
            del current_variants['status']
        if form.is_valid():
            data_form = form.cleaned_data
            user_query = data_form['busqueda']
            LOGGER.info("Datos usuaria query={} idioma={} variantes={}".format(
                        user_query, data_form['idioma'],
                        ', '.join(data_form['variante'])))
            if len(data_form['variante']) != 0:
                q_variant = data_form['variante']
                variantes = " AND variant:" + variant_to_query(q_variant)
            else:
                variantes = ""
            if data_form["idioma"] == "L1":
                idioma = settings.L1.lower()
                lang_query = "l1"
            elif data_form["idioma"] == "L2":
                idioma = settings.L2.lower()
                lang_query = "l2"

            query = query_kreator(f'{lang_query}:({user_query}){variantes}')
            LOGGER.debug("Indice::" + settings.INDEX)
            try:
                r = es.search(index=settings.INDEX, body=query, scroll="1m")
                data_response = r["hits"]
                scroll_id = r["_scroll_id"]
                all_documents = data_response["total"]["value"]
                documents_count = len(data_response["hits"])
                while documents_count != all_documents:
                    sub_response = es.scroll(scroll_id=scroll_id, scroll="1m")
                    data_response["hits"] += sub_response["hits"]["hits"]
                    documents_count += len(sub_response["hits"]["hits"])
                    scroll_id = sub_response["_scroll_id"]
            except elasticsearch.exceptions.RequestError as e:
                LOGGER.error("Error al buscar::{}".format(e))
                LOGGER.error("Query::" + data_form["busqueda"])
                notification = "Tuvimos problemas realizando la búsqueda " + \
                               "<em>" + data_form['busqueda'] + "</em>. " + \
                               "Por favor vuelve a intentarlo :("
                messages.error(request, notification)
                documents_count = 0
            except elasticsearch.exceptions.ConnectionError as e:
                LOGGER.error("Error de conexión::{}".format(e))
                LOGGER.error("No se pudo conectar al Indice de Elasticsearch::" + settings.INDEX)
                notification = "Error de conexión al servidor " + \
                               "Elasticsearch. Intentalo más tarde :("
                messages.error(request, "Error de conexión a servidores :(")
                # TODO: Mandar correos para notificar servers caidos
                documents_count = 0
            if documents_count != 0:
                data = data_processor(data_response, lang_query, user_query)
            else:
                data = []
            return render(request, "searcher/searcher.html",
                          {'form': form, 'data': data,
                           'total': documents_count,
                           'idioma': idioma,
                           'query_text': user_query,
                           'total_variants': len(current_variants)
                           })
    else:
        # Si es metodo GET se redirige a la vista index
        return HttpResponseRedirect('/')

# === Datos de Ethnologue ===


def ethnologue_data(request, iso_variant):
    """**Búsca información de la variante en Ethnologue**

    Trae la información de la página de la variante de Ethnologue. Se scrappea
    con ``BeautifulSoup``. Posteriormente se cra una tabla html con la función
    ``ethno_table_maker``.

    :param request: Objeto ``HttpRequet`` para pasar el estado de la app a
                    través del sistema
    :type: ``HttpRequest``
    :paran iso_variant: ISO de la variante
    :type: str
    :return: ``Html`` con la información disponible de *Ethnologue*
    :rtype: str
    """
    LOGGER.info("Obteniendo información de Ethnologue")
    try:
        r = requests.get(f'https://www.ethnologue.com/language/{iso_variant}')
        if r.status_code != 404:
            html_doc = r.text
            soup = BeautifulSoup(html_doc, 'html.parser')
            return HttpResponse(ethno_table_maker(soup))
        else:
            return HttpResponse(f"<h3>No se encontraron datos :(</h3>")
    except requests.exceptions.ConnectionError as e:
        LOGGER.error("Error de conexión a Ethnologue::{}".format(e.request.body))
        LOGGER.error("Url Ethnologue::" + e.request.url)
        return HttpResponse("<h1>404 :(</h1>")
