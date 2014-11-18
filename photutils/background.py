# Licensed under a 3-clause BSD style license - see LICENSE.rst
from __future__ import (absolute_import, division, print_function,
                        unicode_literals)
import numpy as np
from astropy.stats import sigma_clip
from astropy.utils import lazyproperty


__all__ = ['Background']


__doctest_requires__ = {('Background'): ['scipy']}


class Background(object):
    """
    Class to estimate a 2D background and background rms noise map.

    The background is estimated using sigma-clipped statistics in each
    mesh of a grid that covers the input ``data`` to create a
    low-resolution background map.  The final background map is the
    bicubic spline interpolation of the low-resolution map.

    The exact method used to estimate the background in each mesh can be
    controlled via the ``method`` parameter.  The background rms in each
    mesh is estimated by the sigma-clipped standard deviation.
    """

    def __init__(self, data, box_shape, filter_shape=(3, 3),
                 filter_threshold=None, mask=None, method='sextractor',
                 sigclip_sigma=3., sigclip_iters=None):
        """
        Parameters
        ----------
        data : array_like
            The 2D array from which to estimate the background and/or
            background rms.

        box_shape : 2-tuple of int
            The ``(ny, nx)`` shape of the boxes in which to estimate the
            background.  For best results, the box shape should be
            chosen such than the ``data`` are covered by an integer
            number of boxes in both dimensions.

        filter_shape : 2-tuple of int, optional
            The ``(ny, nx)`` shape of the median filter to apply to the
            background meshes.  A filter shape of ``(1, 1)`` means no
            filtering.

        filter_threshold : int, optional
            The threshold value for used for selective median filtering
            of the background meshes.  If not `None`, then the median
            filter will be applied to only the background meshes with
            values larger than ``filter_threshold``.

        mask : array_like (bool), optional
            A boolean mask, with the same shape as ``data``, where a
            `True` value indicates the corresponding element of ``data``
            is masked.  Masked data are excluded from all calculations.

        method : {'mean', 'median', 'sextractor', 'mode_estimate'}, optional
            The method use to estimate the background in the meshes.
            For all methods, the statistics are calculated from the
            sigma-clipped ``data`` values in each mesh.

            * 'mean':  The mean.
            * 'median':  The median.
            * 'sextractor':  The method used by `SExtractor`_.  The
              background in each mesh is a mode estimator: ``(2.5 *
              median) - (1.5 * mean)``.  If ``(mean - median) / std >
              0.3`` then then median is used instead.  Despite what the
              `SExtractor`_ User's Manual says, this is the method
              it *always* uses.
            * 'mode_estimate':  An alternative mode estimator:
              ``(3 * median) - (2 * mean)``.

        sigclip_sigma : float, optional
            The number of standard deviations to use as the clipping limit
            when calculating the image background statistics.

        sigclip_iters : int, optional
           The number of iterations to perform sigma clipping, or `None` to
           clip until convergence is achieved (i.e., continue until the last
           iteration clips nothing) when calculating the image background
           statistics.

        Notes
        -----
        Limiting ``sigclip_iters`` will speed up the calculations,
        especially for large images, at the cost of some precision.

        .. _SExtractor: http://www.astromatic.net/software/sextractor
        """

        if mask is not None:
            if mask.shape != data.shape:
                raise ValueError('mask shape must match data shape')
        self.box_shape = box_shape
        self.filter_shape = filter_shape
        self.filter_threshold = filter_threshold
        self.mask = mask
        self.method = method
        self.sigclip_sigma = sigclip_sigma
        self.sigclip_iters = sigclip_iters
        self.yextra = data.shape[0] % box_shape[0]
        self.xextra = data.shape[1] % box_shape[1]
        self.data_shape = data.shape
        if (self.yextra > 0) or (self.xextra > 0):
            self.padded = True
            data_ma = self._pad_data(data, mask)
        else:
            self.padded = False
            data_ma = np.ma.masked_array(data, mask=mask)
        self._sigclip_data(data_ma)

    def _pad_data(self, data, mask=None):
        """
        Pad the ``data`` and ``mask`` on the right and top with zeros if
        necessary to have a integer number of background meshes of size
        ``box_shape``.
        """
        ypad, xpad = 0, 0
        if self.yextra > 0:
            ypad = self.box_shape[0] - self.yextra
        if self.xextra > 0:
            xpad = self.box_shape[1] - self.xextra
        pad_width = ((0, ypad), (0, xpad))
        mode = str('constant')
        padded_data = np.pad(data, pad_width, mode=mode,
                             constant_values=[np.nan])
        padded_mask = np.isnan(padded_data)
        if mask is not None:
            mask_pad = np.pad(mask, pad_width, mode=mode,
                              constant_values=[False])
            padded_mask = np.logical_or(padded_mask, mask_pad)
        return np.ma.masked_array(padded_data, mask=padded_mask)

    def _sigclip_data(self, data_ma):
        """
        Perform sigma clipping on the data in regions of size
        ``box_shape``.
        """
        ny, nx = data_ma.shape
        ny_box, nx_box = self.box_shape
        y_nbins = ny / ny_box     # always integer because data were padded
        x_nbins = nx / nx_box     # always integer because data were padded
        data_rebin = np.ma.swapaxes(data_ma.reshape(
            y_nbins, ny_box, x_nbins, nx_box), 1, 2).reshape(y_nbins, x_nbins,
                                                             ny_box * nx_box)
        del data_ma
        self.data_sigclip = sigma_clip(
            data_rebin, sig=self.sigclip_sigma, axis=2,
            iters=self.sigclip_iters, cenfunc=np.ma.median, varfunc=np.ma.var)
        del data_rebin

    def _filter_meshes(self, mesh):
        """
        Apply a 2d median filter to the background meshes, including
        only pixels inside the image at the borders.
        """
        from scipy.ndimage import generic_filter
        if self.filter_threshold is None:
            return generic_filter(mesh, np.nanmedian, size=self.filter_shape,
                                  mode='constant', cval=np.nan)
        else:
            mesh_out = np.copy(mesh)
            for i, j in zip(*np.nonzero(mesh > self.filter_threshold)):
                yfs, xfs = self.filter_shape
                hyfs, hxfs = yfs // 2, xfs // 2
                y0, y1 = max(i - hyfs, 0), min(i - hyfs + yfs, mesh.shape[0])
                x0, x1 = max(j - hxfs, 0), min(j - hxfs + xfs, mesh.shape[1])
                mesh_out[i, j] = np.median(mesh[y0:y1, x0:x1])
            return mesh_out

    def _resize_meshes(self, mesh):
        """
        Resize the background meshes to the original data size using
        bicubic interpolation.
        """
        from scipy.interpolate import RectBivariateSpline
        ny, nx = mesh.shape
        x = np.arange(nx)
        y = np.arange(ny)
        xx = np.linspace(x.min() - 0.5, x.max() + 0.5, self.data_shape[1])
        yy = np.linspace(y.min() - 0.5, y.max() + 0.5, self.data_shape[0])
        return RectBivariateSpline(y, x, mesh, kx=3, ky=3, s=0)(yy, xx)

    @lazyproperty
    def background_mesh(self):
        """
        A 2D `~numpy.ndarray` containing the background estimate in each
        of the meshes of size ``box_shape``.

        This is equivalent to the low-resolution "MINIBACKGROUND"
        background map in `SExtractor`_.
        """
        if self.method == 'mean':
            bkg_mesh = np.ma.mean(self.data_sigclip, axis=2)
        elif self.method == 'median':
            bkg_mesh = np.ma.median(self.data_sigclip, axis=2)
        elif self.method == 'sextractor':
            box_mean = np.ma.mean(self.data_sigclip, axis=2)
            box_median = np.ma.median(self.data_sigclip, axis=2)
            box_std = np.ma.std(self.data_sigclip, axis=2)
            condition = (np.abs(box_mean - box_median) / box_std) < 0.3
            bkg_est = (2.5 * box_median) - (1.5 * box_mean)
            bkg_mesh = np.ma.where(condition, bkg_est, box_median)
        elif self.method == 'mode_estimate':
            bkg_mesh = (3. * np.ma.median(self.data_sigclip, axis=2) -
                        2. * np.ma.mean(self.data_sigclip, axis=2))
        else:
            raise ValueError('method "{0}" is not '
                             'defined'.format(self.method))
        bkg_mesh = np.ma.filled(bkg_mesh, fill_value=np.ma.median(bkg_mesh))
        if self.filter_shape != (1, 1):
            bkg_mesh = self._filter_meshes(bkg_mesh)
        return bkg_mesh

    @lazyproperty
    def background_rms_mesh(self):
        """
        A 2D `~numpy.ndarray` containing the background rms estimate in
        each of the meshes of size ``box_shape``.

        This is equivalent to the low-resolution "MINIBACK_RMS"
        background rms map in `SExtractor`_.
        """
        bkgrms_mesh = np.ma.std(self.data_sigclip, axis=2)
        bkgrms_mesh = np.ma.filled(bkgrms_mesh,
                                   fill_value=np.ma.median(bkgrms_mesh))
        if self.filter_shape != (1, 1):
            bkgrms_mesh = self._filter_meshes(bkgrms_mesh)
        return bkgrms_mesh

    @lazyproperty
    def background(self):
        """
        A 2D `~numpy.ndarray` containing the background estimate.

        This is equivalent to the low-resolution "BACKGROUND" background
        map in `SExtractor`_.
        """
        bkg = self._resize_meshes(self.background_mesh)
        if self.padded:
            y0, x0 = self.data_shape
            bkg = bkg[0:y0, 0:x0]
        if self.mask is not None:
            bkg[self.mask] = 0.
        return bkg

    @lazyproperty
    def background_rms(self):
        """
        A 2D `~numpy.ndarray` containing the background rms estimate.

        This is equivalent to the low-resolution "BACKGROUND_RMS"
        background rms map in `SExtractor`_.
        """
        bkgrms = self._resize_meshes(self.background_rms_mesh)
        if self.padded:
            y0, x0 = self.data_shape
            bkgrms = bkgrms[0:y0, 0:x0]
        if self.mask is not None:
            bkgrms[self.mask] = 0.
        return bkgrms

    @lazyproperty
    def background_median(self):
        """
        The median value of all the background meshes.

        This is equivalent to the value `SExtractor`_ prints to stdout
        (i.e., "(M+D) Background: <value>").
        """
        return np.median(self.background_mesh)

    @lazyproperty
    def background_rms_median(self):
        """
        The median value of all the background rms meshes.

        This is equivalent to the value `SExtractor`_ prints to stdout
        (i.e., "(M+D) RMS: <value>").
        """
        return np.median(self.background_rms_mesh)
