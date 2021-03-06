import numpy as np
import scipy.sparse as sp

from Orange.data import Domain, Table
from Orange.data.util import SharedComputeValue
from Orange.statistics.util import nansum, nanmedian
from Orange.preprocess.preprocess import Preprocess

__all__ = ["SCNormalizer"]


class ScShared(SharedComputeValue):
    """Places the values of shared data within the coresponding variable column."""
    def compute(self, data, shared_data):
        assert self.variable is not None
        return shared_data.get_column_view(self.variable)[0] if self.variable in shared_data.domain else np.nan


class SCNormalizer(Preprocess):
    def __init__(self,
                 equalize_var=None,
                 normalize_cells=True,
                 log_base=2,
                 bin_thresh=None):
        self.equalize_var = equalize_var
        self.normalize_cells = normalize_cells
        self.log_base = log_base
        self.bin_thresh = bin_thresh

    def __call__(self, data):
        proj = ScNormalizeModel(self.equalize_var,
                                self.normalize_cells,
                                self.log_base,
                                self.bin_thresh)
        Y = data.get_column_view(self.equalize_var)[0] if self.equalize_var is not None else None
        proj.fit(data.X, Y)
        attributes = [var.copy(compute_value=ScShared(proj, variable=var))
                      if var.is_continuous else var
                      for var in data.domain.attributes]
        for var in attributes:
            if var.is_continuous:
                var.number_of_decimals = max(3, var.number_of_decimals)
        normalized_domain = Domain(
            attributes, data.domain.class_vars, data.domain.metas)
        return data.transform(normalized_domain)


class ScNormalizeModel:
    """
    Implements model fitting and transformation.
    Parameters inferred from data are stored inside an instance of this class.
     A simple ad-hoc normalization to provide basic raw count pre-processing.
    """

    def __init__(self, equalize_var=None, normalize_cells=True, log_base=2, bin_thresh=None):
        """
        :param equalize_var: Equalization variable.
        :param normalize_cells: Normalize cell profiles.
        :param log_base: Base for log-trasnform. Use None to skip.
        """
        self.equalize_var = equalize_var
        self.normalize_cells = normalize_cells
        self.log_base = log_base
        self.bin_thresh = bin_thresh
        self.target_row_mean = 1
        self.size_factors = {}

    def fit(self, X, Y=None):
        """
        Infer row normalization parameters from the data.
        :param X: Continuous data matrix.
        :param Y: Grouping values
        :return:
        """
        # Equalize based on read depth per library / match mean read count per cell
        # Must not store indices
        if Y is not None:
            libraries = {lib: np.where(Y == lib)[0] for lib in set(Y)}
            lib_sizes = {}
            for lib, rows in libraries.items():
                lib_sizes[lib] = nanmedian(nansum(X[rows, :], axis=1))
            self.target_row_mean = min(lib_sizes.values())
            for lib in libraries:
                self.size_factors[lib] = self.target_row_mean / lib_sizes[lib]
        else:
            self.target_row_mean = nanmedian(nansum(X, axis=1))

    def __call__(self, data):
        """
        :param data: Data to be transformed.
        :return:
        """
        return self.transform(data)

    def transform(self, data):
        """
        Transform data based on inferred parameters.
        :param data: Data table with expression values as counts.
                    Columns are genes and rows are cells.
        :return: Data table with normalized values.
        """
        # Result in expected number of reads
        Xeq = data.X.copy()
        n = Xeq.shape[0]

        # Normalize cell profiles
        if self.normalize_cells:
            # Each cell is normalized independently by default
            if sp.isspmatrix(Xeq):
                rs = Xeq.sum(axis=1).astype(float)
            else:
                rs = nansum(Xeq, axis=1).astype(float)
            rs[rs == 0] = 1.0
            rsm = np.ones((n, ), dtype=float) * self.target_row_mean
            factors = rsm / rs

            # Override with library size factor, if provided. Else, each row is
            # treated as a separate group
            if self.equalize_var is not None:
                vals = np.array(list(map(lambda lib: self.size_factors.get(lib, np.nan),
                                    data.get_column_view(self.equalize_var)[0])))
                inxs = np.logical_not(np.isnan(vals))
                factors[inxs] = vals[inxs]

            Xd = sp.dia_matrix((factors.ravel(), 0), shape=(n, n), dtype=float)
            Xeq = Xd.dot(Xeq)

        # Log transform log(1 + x)
        if self.log_base is not None:
            if sp.isspmatrix(Xeq):
                Xeq = Xeq.log1p() / np.log(self.log_base)
            else:
                Xeq = np.log(1 + Xeq) / np.log(self.log_base)

        # Binary transform;
        # potential change to sparsity structure;
        if self.bin_thresh is not None:
            if sp.isspmatrix(Xeq):
                Xeq.data = (Xeq.data > self.bin_thresh).astype(int)
                Xeq.eliminate_zeros()
            else:
                Xeq = (Xeq > self.bin_thresh)

        # Preserve sparsity
        X_new = Xeq.tocsr() if sp.isspmatrix(Xeq) else Xeq
        data_new = Table.from_numpy(domain=data.domain,
                                    X=X_new,
                                    Y=data.Y,
                                    W=data.W,
                                    metas=data.metas)
        return data_new
