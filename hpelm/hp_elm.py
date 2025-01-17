# -*- coding: utf-8 -*-
"""
Created on Mon Oct 27 17:48:33 2014

@author: akusok
"""

import numpy as np
import multiprocessing as mp
#import Queue
#import threading
from time import time
from hpelm.modules import make_hdf5, ireader, iwriter
from tables import open_file
from slfn import SLFN


class HPELM(SLFN):
    """Interface for training Extreme Learning Machines.
    """

    def _parse_args(self, args, kwargs, X, T):
        """Parse arguments of training, and prepare class variables.

        Model structure selection (exclusive, choose one)
        :param "V": use validation set
        :param "CV": use cross-validation

        Additional parameters for model structure selecation
        :param Xv: validation data X ("V")
        :param Tv: validation targets T ("V")
        :param k: number of splits ("CV")

        System setup
        :param "c": build ELM for classification
        :param "cb": build ELM with balanced classification
        :param "mc": build ELM for multiclass classification
        :param "adaptive"/"ad": build adaptive ELM for non-stationary model
        :param "batch": batch size for adaptive ELM (sliding window step size)
        """

        assert len(self.neurons) > 0, "Add neurons to ELM before training it"
        args = [a.upper() for a in args]  # make all arguments upper case

        # kind of "enumerators", try to use only inside that script
        MODELSELECTION = None  # V / CV / None
        ADAPTIVE = False  # batch / None

        # reset parameters
        self.ranking = None
        self.kmax_op = None
        self.classification = None  # c / wc / mc
        self.weights_wc = None  # weigths for weighted classification

        # check exclusive parameters
        assert len(set(args).intersection(set(["C", "WC", "MC"]))) <= 1, "Use only one of \
            C (classification) / MC (multiclass) / WC (weighted classification)"

        # parse parameters
        for a in args:
            if a == "C":
                assert self.targets > 1, "Classification targets must have 1 output per class"
                self.classification = "c"
            if a == "WC":
                assert self.targets > 1, "Classification targets must have 1 output per class"
                assert "w" in kwargs.keys(), "Provide class weights for weighted classification"
                w = kwargs['w']
                assert len(w) == T.shape[1], "Number of class weights differs from number of target classes"
                self.weights_wc = w
                self.classification = "wc"
            if a == "MC":
                self.classification = "mc"
            # if a in ("A", "AD", "ADAPTIVE"):
            #     assert "batch" in kwargs.keys(), "Provide batch size for adaptive ELM model (batch)"
            #     batch = kwargs['batch']
            #     ADAPTIVE = True


    def train(self, fX, fT, *args, **kwargs):
        """Universal training interface for ELM model with model structure selection.

        :param fX: input data HDF5 or matrix
        :param fT: target data HDF5 or matrix
        """
        X, T = self._checkdata(fX, fT)
        self._parse_args(args, kwargs, X, T)
        # traing the model
        self.Beta = self._project(X, T, solve=True)[2]


    def train_async(self, fX, fT, *args, **kwargs):
        """Universal training interface for ELM model with model structure selection.

        :param fX: input data HDF5 or matrix
        :param fT: target data HDF5 or matrix
        """
        X, T = self._checkdata(fX, fT)
        self._parse_args(args, kwargs, X, T)
        # traing the model
        self.Beta = self._project_async(fX, fT, X, T, solve=True)[2]


    def _makeh5(self, h5name, N, d):
        """Creates HDF5 file opened in append mode.
        """
        make_hdf5((N, d), h5name)
        h5 = open_file(h5name, "a")
        self.opened_hdf5.append(h5)
        for node in h5.walk_nodes():
            pass  # find a node with whatever name
        return node


    def predict(self, fX, fY):
        """Iterative predict which saves data to HDF5, sequential version.
        """
        assert self.Beta is not None, "Train ELM before predicting"
        X, _ = self._checkdata(fX, None)
        N = X.shape[0]
        make_hdf5((N, self.targets), fY)

        h5 = open_file(fY, "a")
        self.opened_hdf5.append(h5)
        for Y in h5.walk_nodes():
            pass  # find a node with whatever name

        nn = np.sum([n1[1] for n1 in self.neurons])
        batch = max(self.batch, nn)
        nb = N / batch  # number of batches
        if N > batch * nb:
            nb += 1

        t = time()
        t0 = time()
        eta = 0

        for b in xrange(0, nb):
            start = b*batch
            stop = min((b+1)*batch, N)

            # get data
            Xb = X[start:stop].astype(np.float64)
            # process data
            Hb = self.project(Xb)
            Yb = Hb.dot(self.Beta)
            # write data
            Y[start:stop] = Yb

            # report time
            eta = int(((time()-t0) / (b+1)) * (nb-b-1))
            if time() - t > self.tprint:
                print "processing batch %d/%d, eta %d:%02d:%02d" % (b+1, nb, eta/3600, (eta % 3600)/60, eta % 60)
                t = time()

        self.opened_hdf5.pop()
        h5.close()


    def predict_async(self, fX, fY):
        """Iterative predict which saves data to HDF5, with asynchronous I/O by separate processes.
        """
        assert self.Beta is not None, "Train ELM before predicting"
        X, _ = self._checkdata(fX, None)
        N = X.shape[0]
        h5 = self.opened_hdf5.pop()
        h5.close()
        make_hdf5((N, self.targets), fY)

        # start async reader and writer for HDF5 files
        qr_in = mp.Queue()
        qr_out = mp.Queue(1)
        reader = mp.Process(target=ireader, args=(fX, qr_in, qr_out))
        reader.daemon = True
        reader.start()
        qw_in = mp.Queue(1)
        writer = mp.Process(target=iwriter, args=(fY, qw_in))
        writer.daemon = True
        writer.start()

        nn = np.sum([n1[1] for n1 in self.neurons])
        batch = max(self.batch, nn)
        nb = N / batch  # number of batches
        if N > batch * nb:
            nb += 1

        t = time()
        t0 = time()
        eta = 0

        for b in xrange(0, nb+1):
            start_next = b*batch
            stop_next = min((b+1)*batch, N)
            # prefetch data
            qr_in.put((start_next, stop_next))  # asyncronous reading of next data batch

            if b > 0:  # first iteration only prefetches data
                # get data
                Xb = qr_out.get()
                Xb = Xb.astype(np.float64)
                # process data
                Hb = self.project(Xb)
                Yb = Hb.dot(self.Beta)
                # save data
                qw_in.put((Yb, start, stop))

            start = start_next
            stop = stop_next
            # report time
            eta = int(((time()-t0) / (b+1)) * (nb-b-1))
            if time() - t > self.tprint:
                print "processing batch %d/%d, eta %d:%02d:%02d" % (b+1, nb, eta/3600, (eta % 3600)/60, eta % 60)
                t = time()

        qw_in.put(None)
        reader.join()
        writer.join()


    def _project(self, X, T, solve=False, wwc=None):
        """Create HH, HT matrices and computes solution Beta.

        HPELM-specific parallel projection.
        Returns solution Beta if solve=True.
        Runs on GPU if self.accelerator="GPU".
        Performs balanced classification if self.classification="cb".
        """
        # initialize
        nn = np.sum([n1[1] for n1 in self.neurons])
        batch = max(self.batch, nn)
        N = X.shape[0]
        nb = N / batch  # number of batches
        if N > batch * nb:
            nb += 1
        HH = np.zeros((nn, nn))  # init data holders
        HT = np.zeros((nn, self.targets))
        if self.classification == "wc":  # weighted classification initialization
            ns = np.zeros((self.targets,))
            for b in xrange(nb):  # batch sum is much faster
                ns += T[b*batch: (b+1)*batch].sum(axis=0)
            wc = (float(ns.sum()) / ns) * self.weights_wc  # class weights normalized to number of samples
            wc = wc**0.5  # because it gets applied twice

        if wwc is not None:
            wc = wwc

        if self.accelerator == "GPU":
            s = self.magma_solver.GPUSolver(nn, self.targets, self.alpha)
        else:
            HH.ravel()[::nn+1] += self.alpha  # add to matrix diagonal trick

        # main loop over all the data
        t = time()
        t0 = time()
        eta = 0
        for b in xrange(nb):
            eta = int(((time()-t0) / (b+0.0000001)) * (nb-b))
            if time() - t > self.tprint:
                print "processing batch %d/%d, eta %d:%02d:%02d" % (b+1, nb, eta/3600, (eta % 3600)/60, eta % 60)
                t = time()
            start = b*batch
            stop = min((b+1)*batch, N)
            Xb = X[start:stop].astype(np.float64)
            Tb = T[start:stop].astype(np.float64)
            Hb = self.project(Xb)

            if self.classification == "wc":  # apply per-sample weighting
                ci = Tb.argmax(1)
                Hb *= wc[ci, None]
                Tb *= wc[ci, None]
            if self.accelerator == "GPU":
                s.add_data(Hb, Tb)
            else:
                HH += np.dot(Hb.T, Hb)
                HT += np.dot(Hb.T, Tb)

        # get computed matrices back
        if self.accelerator == "GPU":
            HH, HT = s.get_corr()
            if solve:
                Beta = s.solve()
            s.finalize()
        elif solve:
            Beta = self._solve_corr(HH, HT)

        # return results
        if solve:
            return HH, HT, Beta
        else:
            return HH, HT


    def _project_async(self, fX, fT, X, T, solve=False, wwc=None):
        """Create HH, HT matrices and computes solution Beta, copy of _project but with async I/O.

        HPELM-specific parallel projection.
        Returns solution Beta if solve=True.
        Runs on GPU if self.accelerator="GPU".
        Performs balanced classification if self.classification="cb".
        """
        # initialize
        nn = np.sum([n1[1] for n1 in self.neurons])
        batch = max(self.batch, nn)
        N = X.shape[0]
        nb = N / batch  # number of batches
        if N > batch * nb:
            nb += 1
        HH = np.zeros((nn, nn))  # init data holders
        HT = np.zeros((nn, self.targets))
        if self.classification == "wc":  # weighted classification initialization
            ns = np.zeros((self.targets,))
            for b in xrange(nb):  # batch sum is much faster
                ns += T[b*batch: (b+1)*batch].sum(axis=0)
            wc = (float(ns.sum()) / ns) * self.weights_wc  # class weights normalized to number of samples
            wc = wc**0.5  # because it gets applied twice

        if wwc is not None:
            wc = wwc

        if self.accelerator == "GPU":
            s = self.magma_solver.GPUSolver(nn, self.targets, self.alpha)
        else:
            HH.ravel()[::nn+1] += self.alpha  # add to matrix diagonal trick

        # close X and T files
        h5 = self.opened_hdf5.pop()
        h5.close()
        h5 = self.opened_hdf5.pop()
        h5.close()

        # start async reader and writer for HDF5 files
        qX_in = mp.Queue()
        qX_out = mp.Queue(1)
        readerX = mp.Process(target=ireader, args=(fX, qX_in, qX_out))
        readerX.daemon = True
        readerX.start()
        qT_in = mp.Queue()
        qT_out = mp.Queue(1)
        readerT = mp.Process(target=ireader, args=(fT, qT_in, qT_out))
        readerT.daemon = True
        readerT.start()

        t = time()
        t0 = time()
        eta = 0

        # main loop over all the data
        for b in xrange(0, nb+1):
            start_next = b*batch
            stop_next = min((b+1)*batch, N)
            # prefetch data
            qX_in.put((start_next, stop_next))  # asyncronous reading of next data batch
            qT_in.put((start_next, stop_next))

            if b > 0:  # first iteration only prefetches data
                Xb = qX_out.get()
                Tb = qT_out.get()
                Xb = Xb.astype(np.float64)
                Tb = Tb.astype(np.float64)

                # process data
                Hb = self.project(Xb)
                if self.classification == "wc":  # apply per-sample weighting
                    ci = Tb.argmax(1)
                    Hb *= wc[ci, None]
                    Tb *= wc[ci, None]
                if self.accelerator == "GPU":
                    s.add_data(Hb, Tb)
                else:
                    HH += np.dot(Hb.T, Hb)
                    HT += np.dot(Hb.T, Tb)
            # report time
            eta = int(((time()-t0) / (b+1)) * (nb-b-1))
            if time() - t > self.tprint:
                print "processing batch %d/%d, eta %d:%02d:%02d" % (b+1, nb, eta/3600, (eta % 3600)/60, eta % 60)
                t = time()

        readerX.join()
        readerT.join()

        # get computed matrices back
        if self.accelerator == "GPU":
            HH, HT = s.get_corr()
            if solve:
                Beta = s.solve()
            s.finalize()
        elif solve:
            Beta = self._solve_corr(HH, HT)

        # return results
        if solve:
            return HH, HT, Beta
        else:
            return HH, HT


    def _error(self, Y1, T1, H1=None, Beta=None, rank=None):
        """Do projection and calculate error in batch mode.

        HPELM-specific iterative error for all usage cases.
        Can be _error(Y, T) or _error(None, T, H, Beta, rank)

        :param T: - true targets for error calculation
        :param H: - projected data for error calculation
        :param Beta: - current projection matrix
        :param rank: - selected neurons (= columns of H)
        """
        if Y1 is None:
            H, T = self._checkdata(H1, T1)
            assert rank.shape[0] == Beta.shape[0], "Wrong dimension of Beta for the given ranking"
            assert T.shape[1] == Beta.shape[1], "Wrong dimension of Beta for the given targets"
            nn = rank.shape[0]
        else:
            _, Y = self._checkdata(None, Y1)
            _, T = self._checkdata(None, T1)
            nn = np.sum([n1[1] for n1 in self.neurons])
        N = T.shape[0]
        batch = max(self.batch, nn)
        nb = N / batch  # number of batches
        if N > batch * nb:
            nb += 1

        if self.classification == "c":
            err = 0
            for b in xrange(nb):
                start = b*batch
                stop = min((b+1)*batch, N)
                Tb = np.array(T[start:stop])
                if Y1 is None:
                    Hb = H[start:stop, rank]
                    Yb = np.dot(Hb, Beta)
                else:
                    Yb = np.array(Y[start:stop])
                errb = np.mean(Yb.argmax(1) != Tb.argmax(1))
                err += errb * float(stop-start)/N

        elif self.classification == "wc":  # weighted classification
            c = T.shape[1]
            errc = np.zeros(c)
            for b in xrange(nb):
                start = b*batch
                stop = min((b+1)*batch, N)
                Tb = np.array(T[start:stop])
                if Y1 is None:
                    Hb = H[start:stop, rank]
                    Yb = np.dot(Hb, Beta)
                else:
                    Yb = np.array(Y[start:stop])
                for i in xrange(c):  # per-class MSE
                    idxc = Tb[:, i] == 1
                    errb = np.mean(Yb[idxc].argmax(1) != i)
                    errc[i] += errb * float(stop-start)/N
            err = np.mean(errc * self.weights_wc)

        elif self.classification == "mc":
            err = 0
            for b in xrange(nb):
                start = b*batch
                stop = min((b+1)*batch, N)
                Tb = np.array(T[start:stop])
                if Y1 is None:
                    Hb = H[start:stop, rank]
                    Yb = np.dot(Hb, Beta)
                else:
                    Yb = np.array(Y[start:stop])
                errb = np.mean((Yb > 0.5) != (Tb > 0.5))
                err += errb * float(stop-start)/N

        else:  # MSE error
            err = 0
            for b in xrange(nb):
                start = b*batch
                stop = min((b+1)*batch, N)
                Tb = T[start:stop]
                if Y1 is None:
                    Hb = H[start:stop, rank]
                    Yb = np.dot(Hb, Beta)
                else:
                    Yb = Y[start:stop]
                errb = np.mean((Tb - Yb)**2)
                err += errb * float(stop-start)/N

        return err


    def train_hpv(self, HH, HT, Xv, Tv, steps=10):
        X, T = self._checkdata(Xv, Tv)
        N = X.shape[0]
        nn = HH.shape[0]

        nns = np.logspace(np.log(3), np.log(nn), steps, base=np.e, endpoint=True)
        nns = np.ceil(nns).astype(np.int)
        nns = np.unique(nns)  # numbers of neurons to check
        print nns
        k = nns.shape[0]
        err = np.zeros((k,))  # errors for these numbers of neurons

        batch = max(self.batch, nn)
        nb = N / batch  # number of batches
        if N > batch * nb:
            nb += 1

        Betas = []  # keep all betas in memory
        for l in nns:
            Betas.append(self._solve_corr(HH[:l, :l], HT[:l, :]))

        t = time()
        t0 = time()
        eta = 0
        for b in xrange(nb):
            eta = int(((time()-t0) / (b+0.0000001)) * (nb-b))
            if time() - t > self.tprint:
                print "processing batch %d/%d, eta %d:%02d:%02d" % (b+1, nb, eta/3600, (eta % 3600)/60, eta % 60)
                t = time()
            start = b*batch
            stop = min((b+1)*batch, N)
            alpha = float(stop-start)/N
            Tb = np.array(T[start:stop])
            Xb = np.array(X[start:stop])
            Hb = self.project(Xb)
            for i in xrange(k):
                hb1 = Hb[:, :nns[i]]
                Yb = np.dot(hb1, Betas[i])
                err[i] += self._error(Yb, Tb) * alpha

        k_opt = np.argmin(err)
        best_nn = nns[k_opt]
        self._prune(np.arange(best_nn))
        self.Beta = Betas[k_opt]
        del Betas
        print "%d of %d neurons selected with a validation set" % (best_nn, nn)
        if best_nn > nn*0.9:
            print "Hint: try re-training with more hidden neurons"
        return nns, err


    def train_myhpv(self, HH, HT, Xv, Tv, steps=10):
        X, T = self._checkdata(Xv, Tv)
        N = X.shape[0]
        nn = HH.shape[0]

        nns = np.logspace(np.log(3), np.log(nn), steps, base=np.e, endpoint=True)
        nns = np.ceil(nns).astype(np.int)
        nns = np.unique(nns)  # numbers of neurons to check
        k = nns.shape[0]
        err = np.zeros((k, 2, 2))  # errors for these numbers of neurons

        batch = max(self.batch, nn)
        nb = N / batch  # number of batches
        if N > batch * nb:
            nb += 1

        Betas = []  # keep all betas in memory
        for l in nns:
            Betas.append(self._solve_corr(HH[:l, :l], HT[:l, :]))

        t = time()
        t0 = time()
        eta = 0
        for b in xrange(nb):
            eta = int(((time()-t0) / (b+0.0000001)) * (nb-b))
            if time() - t > self.tprint:
                print "processing batch %d/%d, eta %d:%02d:%02d" % (b+1, nb, eta/3600, (eta % 3600)/60, eta % 60)
                t = time()
            start = b*batch
            stop = min((b+1)*batch, N)
            Tb = np.array(T[start:stop])
            Xb = np.array(X[start:stop])
            Hb = self.project(Xb)
            Tc = np.argmax(Tb, axis=1)
            for i in xrange(k):
                hb1 = Hb[:, :nns[i]]
                Yb = np.dot(hb1, Betas[i])
                Yc = np.argmax(Yb, axis=1)
                err[i, 0, 0] += np.sum((Tc == 0)*(Yc == 0))
                err[i, 0, 1] += np.sum((Tc == 0)*(Yc == 1))
                err[i, 1, 0] += np.sum((Tc == 1)*(Yc == 0))
                err[i, 1, 1] += np.sum((Tc == 1)*(Yc == 1))

        return nns, err, N





















