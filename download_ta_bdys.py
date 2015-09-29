################################################################################
#
# download_ta_bdys.py
#
# Copyright 2014 Crown copyright (c)
# Land Information New Zealand and the New Zealand Government.
# All rights reserved
#
# This program is released under the terms of the new BSD license. See the 
# LICENSE file for more information.
#
################################################################################

import os
import sys
import re
import json
import string
import socket
import urllib2
import logging.config

from optparse import OptionParser
from ConfigParser import SafeConfigParser

try:
    from osgeo import ogr, osr, gdal
except:
    try:
        import ogr, osr, gdal
    except:
        sys.exit('ERROR: cannot find python OGR and GDAL modules')

version_num = int(gdal.VersionInfo('VERSION_NUM'))
if version_num < 1100000:
    sys.exit('ERROR: Python bindings of GDAL 1.10 or later required')

# make sure gdal exceptions are not silent
gdal.UseExceptions()
osr.UseExceptions()
ogr.UseExceptions()

# translate geometry to 0-360 longitude space
def shift_geom ( geom ):
    if geom is None:
        return
    count = geom.GetGeometryCount()
    if count > 0:
        for i in range( count ):
            shift_geom( geom.GetGeometryRef( i ) )
    else:
        for i in range( geom.GetPointCount() ):
            x, y, z = geom.GetPoint( i )
            if x < 0:
                x = x + 360
            elif x > 360:
                x = x - 360
            geom.SetPoint( i, x, y, z )
    return

#check is geometry ring is clockwise.
def ring_is_clockwise(ring):
    total = 0
    i = 0
    point_count = ring.GetPointCount()
    pt1 = ring.GetPoint(i)
    pt2 = None
    for i in range(point_count-1):
        pt2 = ring.GetPoint(i+1)
        total += (pt2[0] - pt1[0]) * (pt2[1] + pt1[1])
        pt1 = pt2
    return (total >= 0)

# this is required because of a bug in OGR http://trac.osgeo.org/gdal/ticket/5538
def fix_esri_polyon(geom):
    if geom.GetGeometryType() == ogr.wkbMultiPolygon:
        return geom

    polygons = []
    count = geom.GetGeometryCount()
    if count > 0:
        poly = None
        for i in range( count ):
            ring = geom.GetGeometryRef(i)
            if ring_is_clockwise(ring):
                poly = ogr.Geometry(ogr.wkbPolygon)
                poly.AddGeometry(ring)
                polygons.append(poly)
            else:
                poly.AddGeometry(ring)
    new_geom = None
    if  len(polygons) > 1:
        new_geom = ogr.Geometry(ogr.wkbMultiPolygon)
        for poly in polygons:
            new_geom.AddGeometry(poly)
    else:
        new_geom = polygons.pop()
    return new_geom

def main():
    
    usage = "usage: %prog config_file.ini"
    parser = OptionParser(usage=usage)
    (cmd_opt, args) = parser.parse_args()
       
    if len(args) == 1:
        config_files = [args[0]]
    else:
        config_files = ['download_ta_bdys.ini']
    
    parser = SafeConfigParser()
    found = parser.read(config_files)
    if not found:
        sys.exit('Could not load config ' + config_files[0] )
    
    # set up logging
    logging.config.fileConfig(config_files[0], defaults={ 'hostname': socket.gethostname() })
    logger = logging.getLogger()

    logger.info('Starting download TA boundaries')
    
    db_host = None
    db_rolename = None
    db_port = None
    db_user = None
    db_pass = None
    db_schema = 'public'
    layer_name = None
    layer_geom_column = None
    layer_output_srid = 4167
    create_grid = False
    grid_res = 0.05
    shift_geometry = False
    
    base_uri = parser.get('source', 'base_uri')
    db_name = parser.get('database', 'name')
    db_schema = parser.get('database', 'schema')
    
    if parser.has_option('database', 'rolename'):
        db_rolename = parser.get('database', 'rolename')
    if parser.has_option('database', 'host'):
        db_host = parser.get('database', 'host')
    if parser.has_option('database', 'port'):
        db_port = parser.get('database', 'port')
    if parser.has_option('database', 'user'):
        db_user = parser.get('database', 'user')
    if parser.has_option('database', 'password'):
        db_pass = parser.get('database', 'password')
        
    layer_name = parser.get('layer', 'name')
    layer_geom_column = parser.get('layer', 'geom_column')
    if parser.has_option('layer', 'output_srid'):
        layer_output_srid = parser.getint('layer', 'output_srid')
    if parser.has_option('layer', 'create_grid'):
        create_grid = parser.getboolean('layer', 'create_grid')
    if parser.has_option('layer', 'grid_res'):
        grid_res = parser.getfloat('layer', 'grid_res')
    if parser.has_option('layer', 'shift_geometry'):
        shift_geometry = parser.getboolean('layer', 'shift_geometry')
    
    try:
        output_srs = osr.SpatialReference()
        output_srs.ImportFromEPSG(layer_output_srid)
    except:
        logger.fatal("Output SRID %s is not valid" % (layer_output_srid))
        sys.exit(1)
    
    if create_grid and not grid_res > 0:
        logger.fatal("Grid resolution must be greater than 0")
        sys.exit(1)
        
    #
    # Determine TA layer and its year from REST service
    #
    
    logger.debug(base_uri + '?f=json')
    response = urllib2.urlopen(base_uri + '?f=json')
    capabilities = json.load(response)
    
    latest_service = None
    latest_year = None
    p = re.compile('((\d{4})\_Geographies)$', flags = re.UNICODE)
    for service in capabilities['services']:
        m = p.search(service['name'])
        if m:
            if not latest_year or m.group(2) > latest_year:
                latest_year = int(m.group(2))
                latest_service = m.group(1)
    
    logger.debug(base_uri + '/' + latest_service + '/MapServer?f=json')
    response = urllib2.urlopen(base_uri + '/' + latest_service + '/MapServer?f=json')
    capabilities = json.load(response)
    
    ta_layer = None
    p = re.compile('^Territorial\sAuthorities\s\d{4}$', flags = re.UNICODE)
    for layer in capabilities['layers']:
         m = p.search(layer['name'])
         if m:
            ta_layer = layer
            break
        
    if not ta_layer:
        logger.fatal('Could not find the TA layer in ' + base_uri)
        sys.exit(1)
    
    feature_url = base_uri + '/' + latest_service + '/MapServer/' + str(ta_layer['id']) + \
        '/query?f=json&where=1=1&returnGeometry=true&outSR=' + str(layer_output_srid)
    
    geojson_drv = ogr.GetDriverByName('GeoJSON')
    if geojson_drv is None:
        logger.fatal('Could not load the OGR GeoJSON driver')
        sys.exit(1)
    
    #
    # Connect to the PostgreSQL database
    #
    
    pg_drv = ogr.GetDriverByName('PostgreSQL')
    if pg_drv is None:
        logger.fatal('Could not load the OGR PostgreSQL driver')
        sys.exit(1)
    
    pg_uri = 'PG:dbname=' + db_name
    if db_host:
        pg_uri = pg_uri + ' host=' +  db_host
    if db_port:
        pg_uri = pg_uri + ' port=' +  db_port
    if db_user:
        pg_uri = pg_uri + ' user=' +  db_user
    if db_pass:
        pg_uri = pg_uri + ' password=' +  db_pass
    
    pg_ds = None
    try:
        pg_ds = pg_drv.Open(pg_uri, update = 1)
    except Exception, e:
        logger.fatal("Can't open PG output database: " + str(e))
        sys.exit(1)
    
    if db_rolename:
       pg_ds.ExecuteSQL("SET ROLE " + db_rolename)
    
    #
    # Check the current database TA table year. Only continue if data is old.
    #
    
    output_lyr = None
    full_layer_name = db_schema + '.' + layer_name
    try:
        output_lyr = pg_ds.GetLayerByName(full_layer_name)
    except:
        logger.debug(full_layer_name + ' does not exist')
    
    if output_lyr:
        sql = """SELECT
                    description
                 FROM
                    pg_description
                    JOIN pg_class ON pg_description.objoid = pg_class.oid
                    JOIN pg_namespace ON pg_class.relnamespace = pg_namespace.oid
                 WHERE
                    nspname='%s' and
                    relname = '%s' """ % (db_schema, layer_name)
        
        sql_lyr = pg_ds.ExecuteSQL(sql)
        feat = sql_lyr.GetNextFeature()
        if feat:
            current_version = int(feat.GetFieldAsString('description'))
            if current_version <= latest_year:
                logger.info("TA layer does not need to be updated (current version " + \
                    str(current_version) + ")")
                sys.exit(0)
        pg_ds.ReleaseResultSet(sql_lyr)
        
        # truncate data
        pg_ds.ExecuteSQL("TRUNCATE " + full_layer_name)
    
    geojs_ds = None
    try:
        geojs_ds = geojson_drv.Open(feature_url)
    except Exception, e:
        logger.fatal('Could not load fetch feature URL %s: %s' % (feature_url, str(e)))
        sys.exit(1)
    
    input_lyr = geojs_ds.GetLayer(0)
    
    #
    # Create database table if it doesn't already exist.
    #
    
    if not output_lyr:
        create_opts = ['GEOMETRY_NAME='+layer_geom_column]
        if db_schema:
            create_opts.append('SCHEMA=' + db_schema)
        
        try:
            output_lyr = pg_ds.CreateLayer(
                full_layer_name,
                srs = output_srs,
                geom_type = ogr.wkbMultiPolygon,
                options = create_opts
            )
            name_field = ogr.FieldDefn('name', ogr.OFTString)
            name_field.SetWidth(100)
            output_lyr.CreateField(name_field)
            
            pg_ds.ExecuteSQL("GRANT SELECT ON TABLE " + full_layer_name + " TO public")
        except Exception, e:
            logger.fatal('Can not create TA output table: %s' % (str(e)))
            sys.exit(1)
    
    input_defn = input_lyr.GetLayerDefn()
    p = re.compile('^TA\d{4}\_.+\_NAME$', flags = re.UNICODE)
    ta_name_field = None
    for i in range( input_defn.GetFieldCount() ):
        field = input_defn.GetFieldDefn( i )
        field_name = field.GetNameRef()
        if p.search(field_name):
            ta_name_field = field_name
    if not ta_name_field:
        logger.fatal("Can not find TA name field")
        sys.exit(1)
    
    gdal.SetConfigOption('PG_USE_COPY', 'YES')
    
    #
    # Copy data from REST Service to PostgreSQL database
    #
    
    output_defn = output_lyr.GetLayerDefn()
    input_lyr.ResetReading()
    input_feature = input_lyr.GetNextFeature()
    output_lyr.StartTransaction()
    while input_feature is not None:
        output_feature = ogr.Feature(output_defn)
        output_feature['name'] = input_feature[ta_name_field]
        fixed_geom = fix_esri_polyon(input_feature.GetGeometryRef())
        if fixed_geom.GetGeometryType() == ogr.wkbPolygon:
            fixed_geom = ogr.ForceToMultiPolygon(fixed_geom)
        if output_srs.IsGeographic() and shift_geometry:
            shift_geom(fixed_geom)
        output_feature.SetGeometry(fixed_geom)
        output_lyr.CreateFeature(output_feature)
        output_feature.Destroy()
        input_feature = input_lyr.GetNextFeature()
    input_feature = None
    output_lyr.CommitTransaction()
    
    pg_ds.ExecuteSQL("ANALYSE " + full_layer_name)
    pg_ds.ExecuteSQL("COMMENT ON TABLE " + full_layer_name + " IS '" + str(latest_year) + "'")
    
    #
    # Create TA grid index if configured
    #
    
    if create_grid:
        sql = "SELECT create_table_polygon_grid('%s', '%s', '%s', %g, %g) as result" \
              % (db_schema, layer_name, layer_geom_column, grid_res, grid_res)
        logger.debug("Building grid with SQL " + sql)
        try:
            sql_lyr = pg_ds.ExecuteSQL(sql)
            if sql_lyr:
                feat = sql_lyr.GetNextFeature()
                logger.info("Created grid layer: " + feat['result'])
        except Exception, e:
            logger.fatal("Failed to create grid layer: " + str(e))
            sys.exit(1)
    
    logger.info("TA layer have been updated to version " + str(latest_year))
    sys.exit(0)


if __name__ == "__main__":
    main()
