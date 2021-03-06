import logging

import numexpr as ne
import numpy as np
from numpy import ma
from pyg2p import Loggable

from pyg2p.main.interpolation import Interpolator
from pyg2p.main.interpolation.latlong import Dem
from pyg2p.main.readers.grib import GRIBReader

from pyg2p.main.config import GeopotentialsConfiguration
import pyg2p.util.numeric


class Corrector(Loggable):

    instances = {}

    def __repr__(self):
        return f'Corrector<{self.grid_id} {self.geo_file}>'

    @classmethod
    def get_instance(cls, ctx, grid_id):
        geo_file_ = ctx.geo_file(grid_id)
        dem_map = ctx.get('correction.demMap')
        key = f'{grid_id}{dem_map}'
        if key in cls.instances:
            return cls.instances[key]
        else:
            instance = Corrector(ctx, grid_id, geo_file_)
            cls.instances[key] = instance
            return instance

    def __init__(self, ctx, grid_id, geo_file):
        super().__init__()
        self.geo_file = geo_file
        self.grid_id = grid_id
        dem_map = ctx.get('correction.demMap')
        self._dem_missing_value, self._dem_values = self._read_dem(dem_map)
        self._formula = ctx.get('correction.formula')
        self._gem_formula = ctx.get('correction.gemFormula')
        self._numexpr_eval = f'where((dem!=mv) & (p!=mv) & (gem!=mv), {self._formula}, mv)'
        self._numexpr_eval_gem = f'where(z != mv, {self._gem_formula}, mv)'

        log_message = f"""
        Correction
        Reading dem: {dem_map}
        geopotential: {geo_file}
        formula: {self._formula.replace('gem', self._gem_formula)}
        """
        self._log(log_message, 'INFO')

        self._gem_missing_value, self._gem_values = self._read_geo(geo_file, ctx)

    def correct(self, values):
        with np.errstate(over='ignore'):
            # variables below are used by numexpr evaluation namespace
            dem = self._dem_values
            p = values
            gem = self._gem_values
            mv = self._dem_missing_value
            values = ne.evaluate(self._numexpr_eval)
            # mask out values (here is already output values with destination shape)
            values = ma.masked_where(pyg2p.util.numeric.get_masks(p), values)
        return values

    def _read_geo(self, grib_file, ctx):
        is_grib_interpolation = ctx.is_with_grib_interpolation
        reader = GRIBReader(grib_file)
        kwargs = {'shortName': GeopotentialsConfiguration.short_names}
        geopotential_gribs = reader.select_messages(**kwargs)
        missing = geopotential_gribs.missing_value
        values = geopotential_gribs.first_resolution_values()[geopotential_gribs.first_step_range]
        aux_g, aux_v, aux_g2, aux_v2 = reader.get_gids_for_grib_intertable()
        interpolator = Interpolator(ctx, missing)
        interpolator.aux_for_intertable_generation(aux_g, aux_v, aux_g2, aux_v2)

        # get temp from geopotential. will be gem in the formula
        # variables below are used by numexpr evaluation namespace: DO NOT DELETE!
        mv = missing
        z = values
        ne.evaluate(self._numexpr_eval_gem, out=values)

        if is_grib_interpolation:
            values_resampled = interpolator.interpolate_grib(values, -1, self.grid_id)
        else:
            # FOR GEOPOTENTIALS, SOME GRIBS COME WITHOUT LAT/LON GRIDS!
            lats, lons = geopotential_gribs.latlons
            values_resampled = interpolator.interpolate_scipy(lats, lons, values, self.grid_id, geopotential_gribs.grid_details)
        reader.close()
        return missing, values_resampled

    @staticmethod
    def _read_dem(dem_map):
        dem = Dem(dem_map)
        return dem.mv, dem.values
