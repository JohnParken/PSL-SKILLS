#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Encoding-aware, DTD-free XML parsing shared by XLSX entrypoints."""
from xml.parsers import expat
from xml.dom import minidom
from xml.etree import ElementTree as ET


def parse_xml(data, *, dom=False):
    """Reject declarations before either tree builder can expand entities."""
    parser = expat.ParserCreate()

    def reject(*args):
        raise ValueError("DTD/entity declarations are not supported")

    parser.StartDoctypeDeclHandler = reject
    parser.EntityDeclHandler = reject
    parser.ExternalEntityRefHandler = reject
    parser.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)
    try:
        parser.Parse(data, True)
    except expat.ExpatError as exc:
        raise ValueError("Invalid XML: " + str(exc)) from exc
    return minidom.parseString(data) if dom else ET.fromstring(data)
