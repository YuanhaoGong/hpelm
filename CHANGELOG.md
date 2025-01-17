# Change Log
All changes to 'hpelm' toolbox will be documented in this file.

## [0.6.13] - 18-08-2015
### Fixed
- Fixed a bug (typo) which caused HPELM to skip the last batch of data in training/predicting/etc.

## [0.6.14] - 19-08-2015
### New
- Added asyncronous HDF5 file reading in HPELM.predict(). The program must be able to spawn a separate process.

## [0.6.16] - 19-08-2015
### Fixed
- Asyncronous HDF5 file reading now setting "async_io=True" in HPELM.predict(). On Windows, make sure you use 'if __name__ == "__main__"' construction in your main code, or async reader will not start.

## [0.6.20] - 20-08-2015
### New
- Working Asyncronous I/O! for HDF5 files with multiprocessing. Use HPELM.train_async() and HPELM.predict_async() 

## [0.6.21] - 03-09-2015
### New
- Fixed a small GPU function name bug

## [0.6.22] - 03-09-2015
### New
- Fixed confusion matrix bug

