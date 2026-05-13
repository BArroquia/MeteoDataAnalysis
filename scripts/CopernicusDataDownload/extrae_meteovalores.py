"""
Project Workflow: Execution in Docker

    Read the point-layer locations file: LAYER_LOCATIONS.

    Transform coordinates to Lambert Conformal Conic.

    Read all GRIB files in the GRIB_COPERNICUS folder.

    Extract the values for each point and save them to DB_COPERNICUS_OUT.

Technical Constraints & Environment

        Execution: Data processing via Docker (terminal execution).

        Issue: pyproj cannot be installed due to a library database error.

        Issue: pandas cannot be installed due to numpy version conflicts.

Docker Commands

    Access the container via Bash:
    Bash

    docker run --rm -it --name gdalscripts -v "$(pwd):/app" -w /app osgeo/gdal:ubuntu-full-3.6.3 sh

    Run the script:
    Bash

    docker run --rm -it --name gdalscripts -v "$(pwd):/app" -w /app osgeo/gdal:ubuntu-full-3.6.3 python scripts/CopernicusDataDownload/extrae_meteovalores.py
"""

from osgeo import gdal, ogr, osr
import os
import numpy as np
from datetime import datetime
from pathlib import Path

import sqlite3
from datetime import datetime

SPATIALDB_LOCATIONS = './data/interim/municipios_geobiomet.gpkg'
LAYER_LOCATIONS_NAME = 'municipio_prov_centroid_edit'
GRIB_COPERNICUS='./data/download'
DB_COPERNICUS_OUT = "./data/interim/clima2.db"

def inserta_db(ls_datos):
    # --- 1. Conectar a la base de datos ---
    # El archivo "clima.db" se creará en la misma carpeta si no existe.
    nombre_db = DB_COPERNICUS_OUT
    conexion = sqlite3.connect(nombre_db)

    # Crear un 'cursor' para ejecutar comandos SQL
    cursor = conexion.cursor()

    # --- 2. Crear la tabla si no existe ---
    # Usar 'IF NOT EXISTS' es una buena práctica para que el script no falle si ya existe.
    # Se añade una columna 'id' que será la clave primaria autoincremental.
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS copernicus (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        valor REAL NOT NULL,
        variable TEXT NOT NULL,
        fecha TEXT NOT NULL,
        provincia TEXT NOT NULL
    )
    ''')

    # --- 3. Preparar los datos que se van a insertar ---
    # Una lista de tuplas es el formato ideal para insertar múltiples registros.
    # Usamos datetime.now().isoformat() para guardar la fecha como texto estándar.

    # --- 4. Insertar los datos en la tabla ---
    # Se usan '?' como marcadores de posición para prevenir inyecciones SQL.
    # 'executemany' es eficiente para insertar varias filas a la vez.
    sql = "INSERT INTO copernicus (valor, variable, fecha, provincia) VALUES (?, ?, ?, ?)"
    cursor.executemany(sql, ls_datos)

    # --- 5. Confirmar (guardar) los cambios en la base de datos ---
    conexion.commit()
    # print(f"Se han insertado {cursor.rowcount} registros con éxito en la tabla 'copernicus'.")
    # --- 7. Cerrar la conexión con la base de datos ---
    conexion.close()

def transform_etrs_lamb(x, y) -> tuple:
    """
    Devuelve X, Y en proyección lambert cónica.
    La trasnformación es directa y comprobada.
    """
    # --- 1. Definir los Sistemas de Coordenadas (SRC) ---
    # SRC de Origen: ETRS89 / UTM zone 30N
    srs_origen = osr.SpatialReference()
    srs_origen.ImportFromEPSG(25830)
    # SRC de Destino: Proyectada Lambert cónica
    proj_params = (
        "+proj=lcc +lat_0=50 +lat_1=50 +lat_2=50 +lon_0=8 "
        "+x_0=0 +y_0=0 +R=6371229 +units=m +no_defs"
    )
    srs_destino = osr.SpatialReference()
    srs_destino.ImportFromProj4(proj_params)
    # --- 2. Crear el objeto de transformación ---
    transformacion = osr.CoordinateTransformation(srs_origen, srs_destino)
    # --- 4. Crear la geometría y aplicar la transformación ---
    # Crear un objeto de geometría de tipo punto
    punto = ogr.Geometry(ogr.wkbPoint)
    # Añadir las coordenadas de origen
    punto.AddPoint(x, y)

    # print(f"Coordenadas de origen (EPSG:25830): x={punto.GetX()}, y={punto.GetY()}")
    # Aplicar la transformación (modifica el objeto 'punto' directamente)
    punto.Transform(transformacion)
    # --- 5. Obtener el resultado ---
    # Extraer las coordenadas ya transformadas
    x_proy = punto.GetX()
    y_proy = punto.GetY()
    # print(f"Coordenadas transformadas Lambert cónica: x={x_proy:.6f}, y={y_proy:.6f}")
    return x_proy, y_proy

def get_locations(ruta_gp):
    """
    Localizaciones capa de puntos para extraer valores
    """
    layer_name = LAYER_LOCATIONS_NAME
    dc_feat = {}
    driver = ogr.GetDriverByName("GPKG")
    shp = driver.Open(ruta_gp, 0)
    lyr = shp.GetLayerByName(layer_name)
    if lyr is None:
        print(f"Error: No se encontró la capa '{layer_name}' en el fichero.")
    for FID, feat in enumerate(lyr):
        dc_feat[FID] = {}
        dc_feat[FID]['entity'] = feat.GetField("NAMEUNIT")
        dc_feat[FID]['cod_entity'] = feat.GetField("provincia")
        geometria = feat.GetGeometryRef()
        # Comprobar que la geometría existe y es un punto
        if geometria is not None and geometria.GetGeometryType() == ogr.wkbPoint:
            # 5. Extraer las coordenadas del punto
            dc_feat[FID]['x'] = geometria.GetX()
            dc_feat[FID]['y'] = geometria.GetY()
            x_lamb, y_lamb = transform_etrs_lamb(dc_feat[FID]['x'], dc_feat[FID]['y'])
            dc_feat[FID]['x_pixel'] = x_lamb
            dc_feat[FID]['y_pixel'] = y_lamb
    shp = None
    return dc_feat


def get_value_band(banda, columna, fila):
    # 4. Leer el valor del píxel de la primera banda
    metadata = banda.GetMetadata()
    # matriz_numpy = banda.ReadAsArray()
    valor = banda.ReadAsArray(columna, fila, 1, 1)
    fecha_string = metadata.get('GRIB_IDS').split(' ')[-3].split('=')[-1]
    fecha_objeto = datetime.fromisoformat(fecha_string.replace('Z', '+00:00'))
    # print(round(valor[0][0],2), metadata.get('GRIB_ELEMENT', 'N/A'),
    #       fecha_objeto)
    # El resultado de ReadAsArray es un array 2D de NumPy, extraemos el valor
    return [round(valor[0][0],2), metadata.get('GRIB_ELEMENT', 'N/A'), fecha_objeto.isoformat()]

def almacena_valor_pixel(ruta_raster, longitud, latitud, provincia):
    """
    Obtiene el valor de un píxel de un ráster a partir de coordenadas geográficas.

    Args:
        ruta_raster (str): La ruta al fichero ráster (ej. GeoTIFF).
        longitud (float): La coordenada de longitud (X).
        latitud (float): La coordenada de latitud (Y).

    Returns:
        El valor del píxel en la primera banda, o None si las coordenadas están fuera de la extensión.
    """
    ls_val = []
    # 1. Abrir el fichero ráster
    dataset = gdal.Open(ruta_raster, gdal.GA_ReadOnly)
    if not dataset:
        print(f"Error: No se pudo abrir el fichero: {ruta_raster}")
        return None

    # 2. Obtener la geotransformación del ráster
    gt = dataset.GetGeoTransform()
    inv_gt = gdal.InvGeoTransform(gt)
    if inv_gt is None:
        print("Error al invertir la geotransformación.")
        return None

    # 3. Transformar las coordenadas geográficas a coordenadas de píxel
    columna = int(inv_gt[0] + inv_gt[1] * longitud + inv_gt[2] * latitud)
    fila = int(inv_gt[3] + inv_gt[4] * longitud + inv_gt[5] * latitud)

    # Comprobar si las coordenadas del píxel están dentro de los límites del ráster
    ancho = dataset.RasterXSize
    alto = dataset.RasterYSize

    if columna < 0 or columna >= ancho or fila < 0 or fila >= alto:
        print("Las coordenadas están fuera de la extensión del ráster.")
        return None

    numero_de_bandas = dataset.RasterCount
    for i in range(1, numero_de_bandas + 1):
        band_i = dataset.GetRasterBand(i)
        val = get_value_band(band_i, columna, fila)
        val.append(provincia)
        ls_val.append(tuple(val))
    inserta_db(ls_datos=ls_val)



def listar_ficheros_txt(ruta_relativa: str) -> list:
    """
    Busca en una ruta relativa y devuelve una lista de ficheros con extensión .txt.

    Args:
        ruta_relativa: La ruta a la carpeta donde buscar.

    Returns:
        Una lista de strings con las rutas a los ficheros .txt encontrados.
        Devuelve una lista vacía si la carpeta no existe o no hay ficheros.
    """
    try:
        # 1. Crear un objeto Path a partir de la ruta relativa
        ruta = Path(ruta_relativa)
        
        # 2. Comprobar si la ruta existe y es un directorio
        if not ruta.is_dir():
            print(f"Advertencia: La ruta '{ruta_relativa}' no existe o no es un directorio.")
            return []

        # 3. Usar glob para encontrar los ficheros y devolver una lista de strings
        ficheros_txt = [str(fichero) for fichero in ruta.glob('*.grib')]
        
        return ficheros_txt
    except Exception as e:
        print(f"Ha ocurrido un error inesperado: {e}")
        return []

def almacena_db_valores_copernicus_grib(dc_loc, ruta_del_raster):
    for k, v in dc_loc.items():
        # print(k, v['x_pixel'], v['y_pixel'],  v['cod_entity'])
        almacena_valor_pixel(ruta_del_raster, v['x_pixel'], v['y_pixel'],  v['cod_entity'])

if __name__ == "__main__":    
    ruta_gp = SPATIALDB_LOCATIONS
    dc_loc = get_locations(ruta_gp)
    files = listar_ficheros_txt(ruta_relativa=GRIB_COPERNICUS)
    lsfiles = []
    for file in files:
        if '2018' not in file.split('_')[0]:
            lsfiles.append(file) 
    for i, file in enumerate(lsfiles):
        print("\t - ", file, f" --> {i+1}/{len(lsfiles)}")
        almacena_db_valores_copernicus_grib(dc_loc=dc_loc, ruta_del_raster=file)

    print("--END--")