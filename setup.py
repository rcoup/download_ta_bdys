#!/usr/bin/env python
################################################################################
#
# setup.py
#
# Copyright 2014 Crown copyright (c)
# Land Information New Zealand and the New Zealand Government.
# All rights reserved
#
# This program is released under the terms of the new BSD license. See the 
# LICENSE file for more information.
#
################################################################################

from distutils.core import setup

setup(
    name ='linz-download_ta_bdys',
    version = '1.0',
    description ='LINZ software to download NZ Territorial Authority boundaries to a PostgreSQL/PostGIS Database',
    author ='Jeremy Palmer',
    author_email ='jpalmer@linz.govt.nz',
    url= 'http://www.linz.govt.nz',
    scripts=['download_ta_bdys.py'],
    data_files=[('/etc', ['download_ta_bdys.ini'])]
)
