# Sentinel-2 package

import ee
from Py6S import *
import math
import datetime
import os, sys
from utils import *
import sun_angles
import view_angles
import time

class env(object):

	def __init__(self):
		"""Initialize the environment."""

		# Initialize the Earth Engine object, using the authentication credentials.
		ee.Initialize()
		
		self.dem = ee.Image("USGS/SRTMGL1_003")
		self.epsg = "EPSG:32717"
				
		##########################################
		# variable for the landsat data request #
		##########################################
		self.metadataCloudCoverMax = 60;

		##########################################
		# Export variables		  		         #
		##########################################		

		self.assetId ="projects/Sacha/L8/"
		self.name = "landsat_SR_Biweek_" 
		self.exportScale = 30		
		
		##########################################
		# variable for the shadowMask  algorithm #
		##########################################
	
		# zScoreThresh: Threshold for cloud shadow masking- lower number masks out 
		# less. Between -0.8 and -1.2 generally works well
		self.zScoreThresh = -1

		# shadowSumThresh: Sum of IR bands to include as shadows within TDOM and the 
		# shadow shift method (lower number masks out less)
		self.shadowSumThresh = 0.35;
		
		# contractPixels: The radius of the number of pixels to contract (negative buffer) clouds and cloud shadows by. Intended to eliminate smaller cloud 
		#    patches that are likely errors (1.5 results in a -1 pixel buffer)(0.5 results in a -0 pixel buffer)
		# (1.5 or 2.5 generally is sufficient)
		self.contractPixels = 1.5; 
		
		# dilatePixels: The radius of the number of pixels to dilate (buffer) clouds 
		# and cloud shadows by. Intended to include edges of clouds/cloud shadows 
		# that are often missed (1.5 results in a 1 pixel buffer)(0.5 results in a 0 pixel buffer)
		# (2.5 or 3.5 generally is sufficient)
		self.dilatePixels = 2.5;	
		
		
		##########################################
		# variable for cloudScore  algorithm     #
		##########################################	
		
		# 9. Cloud and cloud shadow masking parameters.
		# If cloudScoreTDOM is chosen
		# cloudScoreThresh: If using the cloudScoreTDOMShift method-Threshold for cloud 
		#    masking (lower number masks more clouds.  Between 10 and 30 generally works best)
		self.cloudScoreThresh = 20;
		
		# Percentile of cloud score to pull from time series to represent a minimum for 
		# the cloud score over time for a given pixel. Reduces commission errors over 
		# cool bright surfaces. Generally between 5 and 10 works well. 0 generally is a bit noisy	
		self.cloudScorePctl = 5; 	
		self.hazeThresh = 200
		
		##########################################
		# variable for terrain  algorithm        #
		##########################################		
		
		self.terrainScale = 300
		
		##########################################
		# variable band selection  		         #
		##########################################		
		
		self.divideBands = ee.List(['blue','green','red','nir','swir1','swir2'])
		self.bandNamesLandsat = ee.List(['blue','green','red','nir','swir1','thermal','swir2','sr_atmos_opacity','pixel_qa','radsat_qa'])
		self.sensorBandDictLandsatSR = ee.Dictionary({'L8' : ee.List([1,2,3,4,5,7,6,9,10,11])})
        
        
		##########################################
		# enable / disable modules 		         #
		##########################################		  
		self.maskSR = True
		self.cloudMask = True
		self.hazeMask = True
		self.shadowMask = True
		self.brdfCorrect = True
		self.terrainCorrection = True

class functions():       
	def __init__(self):
		"""Initialize the Surfrace Reflectance app."""  
 
	    # get the environment
		self.env = env() 
	
	def main(self,studyArea,startDate,endDate,startDay,endDay,week):
		
		self.env.startDate = startDate
		self.env.endDate = endDate
		
		self.env.startDoy = startDay
		self.env.endDoy = endDay
		
		#studyArea = ee.FeatureCollection("users/apoortinga/countries/Ecuador_nxprovincias").geometry().bounds();
		
		landsat8 = ee.ImageCollection('LANDSAT/LC08/C01/T1_SR').filterDate(self.env.startDate,self.env.endDate).filterBounds(studyArea)
		landsat8 = landsat8.filterMetadata('CLOUD_COVER','less_than',self.env.metadataCloudCoverMax)
		landsat8 = landsat8.select(self.env.sensorBandDictLandsatSR.get('L8'),self.env.bandNamesLandsat)
		
		print(landsat8.size().getInfo())
		
		if landsat8.size().getInfo() > 0:

			# mask clouds using the QA band
			if self.env.maskSR == True:
				print("removing clouds")
				landsat8 = landsat8.map(self.CloudMaskSRL8)    
					
			# mask clouds using cloud mask function
			if self.env.hazeMask == True:
				print("removing haze")
				landsat8 = landsat8.map(self.maskHaze)

			# mask clouds using cloud mask function
			if self.env.shadowMask == True:
				print("shadow masking")
				landsat8 = self.maskShadows(landsat8,studyArea)		
			
			landsat8 = landsat8.map(self.scaleLandsat)

			# mask clouds using cloud mask function
			if self.env.cloudMask == True:
				print("removing some more clouds")
				landsat8 = landsat8.map(self.maskClouds)
					
			if self.env.brdfCorrect == True:
				landsat8 = landsat8.map(self.brdf)
						
			if self.env.terrainCorrection == True:
				print("terrain correction")
				landsat8 = ee.ImageCollection(landsat8.map(self.terrain))
			
			print("calculating medoid")
			img = self.medoidMosaic(landsat8)
						
			print("rescale")
			img = self.reScaleLandsat(img)
						
			print("set MetaData")
			img = self.setMetaData(img)
			
			print("exporting composite")
			self.exportMap(img,studyArea,week)

	def CloudMaskSRL8(self,img):
		"""apply cf-mask Landsat""" 
		QA = img.select("pixel_qa")
		
		shadow = QA.bitwiseAnd(8).neq(0);
		cloud =  QA.bitwiseAnd(32).neq(0);
		return img.updateMask(shadow.Not()).updateMask(cloud.Not()).copyProperties(img)		
         
	def scaleLandsat(self,img):
		"""Landast is scaled by factor 0.0001 """
		thermal = img.select(ee.List(['thermal'])).multiply(0.1)
		scaled = ee.Image(img).select(self.env.divideBands).multiply(ee.Number(0.0001))
		
		return img.select([]).addBands(scaled).addBands(thermal)
		
	def reScaleLandsat(self,img):
		"""Landast is scaled by factor 0.0001 """
        
		thermalBand = ee.List(['thermal'])
		thermal = ee.Image(img).select(thermalBand).multiply(10)
                
		otherBands = ee.Image(img).bandNames().removeAll(thermalBand)
		scaled = ee.Image(img).select(otherBands).divide(0.0001)
        
		image = ee.Image(scaled.addBands(thermal)).int16()
        
		return image.copyProperties(img)

	def maskHaze(self,img):
		""" mask haze """
		opa = ee.Image(img.select(['sr_atmos_opacity']).multiply(0.001))
		haze = opa.gt(self.env.hazeThresh)
		return img.updateMask(haze.Not())
 

	def maskClouds(self,img):
		"""
		Computes spectral indices of cloudyness and take the minimum of them.
		
		Each spectral index is fairly lenient because the group minimum 
		is a somewhat stringent comparison policy. side note -> this seems like a job for machine learning :)
		originally written by Matt Hancher for Landsat imageryadapted to Sentinel by Chris Hewig and Ian Housman
		"""
		
		score = ee.Image(1.0);
		# Clouds are reasonably bright in the blue band.
		blue_rescale = img.select('blue').subtract(ee.Number(0.1)).divide(ee.Number(0.3).subtract(ee.Number(0.1)))
		score = score.min(blue_rescale);

		# Clouds are reasonably bright in all visible bands.
		visible = img.select('red').add(img.select('green')).add(img.select('blue'))
		visible_rescale = visible.subtract(ee.Number(0.2)).divide(ee.Number(0.8).subtract(ee.Number(0.2)))
		score = score.min(visible_rescale);

		# Clouds are reasonably bright in all infrared bands.
		infrared = img.select('nir').add(img.select('swir1')).add(img.select('swir2'))
		infrared_rescale = infrared.subtract(ee.Number(0.3)).divide(ee.Number(0.8).subtract(ee.Number(0.3)))
		score = score.min(infrared_rescale);

		# Clouds are reasonably cool in temperature.
		temp_rescale = img.select('thermal').subtract(ee.Number(300)).divide(ee.Number(290).subtract(ee.Number(300)))
		score = score.min(temp_rescale);

		# However, clouds are not snow.
		ndsi = img.normalizedDifference(['green', 'swir1']);
		ndsi_rescale = ndsi.subtract(ee.Number(0.8)).divide(ee.Number(0.6).subtract(ee.Number(0.8)))
		score =  score.min(ndsi_rescale).multiply(100).byte();
		mask = score.lt(self.env.cloudScoreThresh).rename(['cloudMask']);
		img = img.updateMask(mask);
        
		return img;
        
	def maskShadows(self,collection,studyArea):

		def TDOM(image):
			zScore = image.select(shadowSumBands).subtract(irMean).divide(irStdDev)
			irSum = image.select(shadowSumBands).reduce(ee.Reducer.sum())
			TDOMMask = zScore.lt(self.env.zScoreThresh).reduce(ee.Reducer.sum()).eq(2)\
				.And(irSum.lt(self.env.shadowSumThresh)).Not()
			TDOMMask = TDOMMask.focal_min(self.env.dilatePixels)
			
			return image.updateMask(TDOMMask)
			
		shadowSumBands = ['nir','swir1']

		self.fullCollection = ee.ImageCollection('LANDSAT/LC08/C01/T1_SR').filterBounds(studyArea).select(self.env.sensorBandDictLandsatSR.get('L8'),self.env.bandNamesLandsat)  

		# Get some pixel-wise stats for the time series
		irStdDev = self.fullCollection.select(shadowSumBands).reduce(ee.Reducer.stdDev())
		irMean = self.fullCollection.select(shadowSumBands).reduce(ee.Reducer.mean())

		# Mask out dark dark outliers
		collection_tdom = collection.map(TDOM)

		return collection_tdom


	def terrain(self,img):   
		degree2radian = 0.01745;

		thermalBand = img.select(['thermal'])
 
		def topoCorr_IC(img):
			
			dem = ee.Image("USGS/SRTMGL1_003")
			
			
			# Extract image metadata about solar position
			SZ_rad = ee.Image.constant(ee.Number(img.get('SOLAR_ZENITH_ANGLE'))).multiply(degree2radian).clip(img.geometry().buffer(10000)); 
			SA_rad = ee.Image.constant(ee.Number(img.get('SOLAR_AZIMUTH_ANGLE'))).multiply(degree2radian).clip(img.geometry().buffer(10000)); 
			
				
			# Creat terrain layers
			slp = ee.Terrain.slope(dem).clip(img.geometry().buffer(10000));
			slp_rad = ee.Terrain.slope(dem).multiply(degree2radian).clip(img.geometry().buffer(10000));
			asp_rad = ee.Terrain.aspect(dem).multiply(degree2radian).clip(img.geometry().buffer(10000));
  
  
			
			# Calculate the Illumination Condition (IC)
			# slope part of the illumination condition
			cosZ = SZ_rad.cos();
			cosS = slp_rad.cos();
			slope_illumination = cosS.expression("cosZ * cosS", \
												{'cosZ': cosZ, 'cosS': cosS.select('slope')});
			
			
			# aspect part of the illumination condition
			sinZ = SZ_rad.sin(); 
			sinS = slp_rad.sin();
			cosAziDiff = (SA_rad.subtract(asp_rad)).cos();
			aspect_illumination = sinZ.expression("sinZ * sinS * cosAziDiff", \
                                           {'sinZ': sinZ, \
                                            'sinS': sinS, \
                                            'cosAziDiff': cosAziDiff});
			
			# full illumination condition (IC)
			ic = slope_illumination.add(aspect_illumination);
			
			

			# Add IC to original image
			img_plus_ic = ee.Image(img.addBands(ic.rename(['IC'])).addBands(cosZ.rename(['cosZ'])).addBands(cosS.rename(['cosS'])).addBands(slp.rename(['slope'])));
			
			return ee.Image(img_plus_ic);
 
		def topoCorr_SCSc(img):
			img_plus_ic = img;
			mask1 = img_plus_ic.select('nir').gt(-0.1);
			mask2 = img_plus_ic.select('slope').gte(5) \
                            .And(img_plus_ic.select('IC').gte(0)) \
                            .And(img_plus_ic.select('nir').gt(-0.1));

			img_plus_ic_mask2 = ee.Image(img_plus_ic.updateMask(mask2));

			bandList = ['blue', 'green', 'red', 'nir', 'swir1', 'swir2']; # Specify Bands to topographically correct
    

			def applyBands(image):
				blue = apply_SCSccorr('blue').select(['blue'])
				green = apply_SCSccorr('green').select(['green'])
				red = apply_SCSccorr('red').select(['red'])
				nir = apply_SCSccorr('nir').select(['nir'])
				swir1 = apply_SCSccorr('swir1').select(['swir1'])
				swir2 = apply_SCSccorr('swir2').select(['swir2'])
				return replace_bands(image, [blue, green, red, nir, swir1, swir2])

			def apply_SCSccorr(band):
				method = 'SCSc';
		
				out = img_plus_ic_mask2.select('IC', band).reduceRegion(reducer= ee.Reducer.linearFit(), \
																		geometry= ee.Geometry(img.geometry().buffer(-5000)), \
																		scale= self.env.terrainScale, \
																		maxPixels = 1e13); 

				out_a = ee.Number(out.get('scale'));
				out_b = ee.Number(out.get('offset'));
				out_c = ee.Number(out.get('offset')).divide(ee.Number(out.get('scale')));
				
				# apply the SCSc correction
				SCSc_output = img_plus_ic_mask2.expression("((image * (cosB * cosZ + cvalue)) / (ic + cvalue))", {
															'image': img_plus_ic_mask2.select([band]),
															'ic': img_plus_ic_mask2.select('IC'),
															'cosB': img_plus_ic_mask2.select('cosS'),
															'cosZ': img_plus_ic_mask2.select('cosZ'),
															'cvalue': out_c });
      
				return ee.Image(SCSc_output);
																  
			#img_SCSccorr = ee.Image([apply_SCSccorr(band) for band in bandList]).addBands(img_plus_ic.select('IC'));
			img_SCSccorr = applyBands(img).select(bandList).addBands(img_plus_ic.select('IC'))
		
			bandList_IC = ee.List([bandList, 'IC']).flatten();
			
			img_SCSccorr = img_SCSccorr.unmask(img_plus_ic.select(bandList_IC)).select(bandList);
  			
			return img_SCSccorr.unmask(img_plus_ic.select(bandList)) 
	
		
		
		img = topoCorr_IC(img)
		img = topoCorr_SCSc(img)
		
		return img.addBands(thermalBand)
  	
 
	def brdf(self,img):   
		
		import sun_angles
		import view_angles

	
		def _apply(image, kvol, kvol0):
			blue = _correct_band(image, 'blue', kvol, kvol0, f_iso=0.0774, f_geo=0.0079, f_vol=0.0372)
			green = _correct_band(image, 'green', kvol, kvol0, f_iso=0.1306, f_geo=0.0178, f_vol=0.0580)
			red = _correct_band(image, 'red', kvol, kvol0, f_iso=0.1690, f_geo=0.0227, f_vol=0.0574)
			nir = _correct_band(image, 'nir', kvol, kvol0, f_iso=0.3093, f_geo=0.0330, f_vol=0.1535)
			swir1 = _correct_band(image, 'swir1', kvol, kvol0, f_iso=0.3430, f_geo=0.0453, f_vol=0.1154)
			swir2 = _correct_band(image, 'swir2', kvol, kvol0, f_iso=0.2658, f_geo=0.0387, f_vol=0.0639)
			return replace_bands(image, [blue, green, red, nir, swir1, swir2])


		def _correct_band(image, band_name, kvol, kvol0, f_iso, f_geo, f_vol):
			"""fiso + fvol * kvol + fgeo * kgeo"""
			iso = ee.Image(f_iso)
			geo = ee.Image(f_geo)
			vol = ee.Image(f_vol)
			pred = vol.multiply(kvol).add(geo.multiply(kvol)).add(iso).rename(['pred'])
			pred0 = vol.multiply(kvol0).add(geo.multiply(kvol0)).add(iso).rename(['pred0'])
			cfac = pred0.divide(pred).rename(['cfac'])
			corr = image.select(band_name).multiply(cfac).rename([band_name])
			return corr


		def _kvol(sunAz, sunZen, viewAz, viewZen):
			"""Calculate kvol kernel.
			From Lucht et al. 2000
			Phase angle = cos(solar zenith) cos(view zenith) + sin(solar zenith) sin(view zenith) cos(relative azimuth)"""
			
			relative_azimuth = sunAz.subtract(viewAz).rename(['relAz'])
			pa1 = viewZen.cos() \
				.multiply(sunZen.cos())
			pa2 = viewZen.sin() \
				.multiply(sunZen.sin()) \
				.multiply(relative_azimuth.cos())
			phase_angle1 = pa1.add(pa2)
			phase_angle = phase_angle1.acos()
			p1 = ee.Image(PI().divide(2)).subtract(phase_angle)
			p2 = p1.multiply(phase_angle1)
			p3 = p2.add(phase_angle.sin())
			p4 = sunZen.cos().add(viewZen.cos())
			p5 = ee.Image(PI().divide(4))

			kvol = p3.divide(p4).subtract(p5).rename(['kvol'])

			viewZen0 = ee.Image(0)
			pa10 = viewZen0.cos() \
				.multiply(sunZen.cos())
			pa20 = viewZen0.sin() \
				.multiply(sunZen.sin()) \
				.multiply(relative_azimuth.cos())
			phase_angle10 = pa10.add(pa20)
			phase_angle0 = phase_angle10.acos()
			p10 = ee.Image(PI().divide(2)).subtract(phase_angle0)
			p20 = p10.multiply(phase_angle10)
			p30 = p20.add(phase_angle0.sin())
			p40 = sunZen.cos().add(viewZen0.cos())
			p50 = ee.Image(PI().divide(4))

			kvol0 = p30.divide(p40).subtract(p50).rename(['kvol0'])

			return (kvol, kvol0)
         
		date = img.date()
		footprint = determine_footprint(img)
		(sunAz, sunZen) = sun_angles.create(date, footprint)
		(viewAz, viewZen) = view_angles.create(footprint)
		(kvol, kvol0) = _kvol(sunAz, sunZen, viewAz, viewZen)
		return _apply(img, kvol.multiply(PI()), kvol0.multiply(PI()))

 			
	def medoidMosaic(self,collection):
		""" medoid composite with equal weight among indices """

		bandNames = ee.Image(collection.first()).bandNames()
		otherBands = bandNames.removeAll(self.env.divideBands)

		others = collection.select(otherBands).reduce(ee.Reducer.mean()).rename(otherBands);
		
		collection = collection.select(self.env.divideBands)

		bandNumbers = ee.List.sequence(1,self.env.divideBands.length());

		median = ee.ImageCollection(collection).median()
        
		def subtractmedian(img):
			diff = ee.Image(img).subtract(median).pow(ee.Image.constant(2));
			return diff.reduce('sum').addBands(img);
        
		medoid = collection.map(subtractmedian)
  
		medoid = ee.ImageCollection(medoid).reduce(ee.Reducer.min(self.env.divideBands.length().add(1))).select(bandNumbers,self.env.divideBands);
  
		return medoid.addBands(others);		

	def medianMosaic(self,collection):
		
		""" median composite """ 
		median = collection.select(medianIncludeBands).median();
		othersBands = bandNames.removeAll(medianIncludeBands);
		others = collection.select(otherBands).mean();
    
		return median.addBands(others)


	def setMetaData(self,img):
		""" add metadata to image """
		
		img = ee.Image(img).set({'system:time_start':ee.Date(self.env.startDate).millis(), \
								 'startDOY':str(self.env.startDoy), \
								 'endDOY':str(self.env.endDoy), \
								 'useCloudScore':str(self.env.cloudMask), \
								 'useTDOM':str(self.env.shadowMask), \
								 'useSRmask':str(self.env.maskSR ), \
								 'useCloudProject':str(self.env.cloudMask), \
								 'terrain':str(self.env.terrainCorrection), \
								 'cloudScoreThresh':str(self.env.cloudScoreThresh), \
								 'cloudScorePctl':str(self.env.cloudScorePctl), \
								 'zScoreThresh':str(self.env.zScoreThresh), \
								 'shadowSumThresh':str(self.env.shadowSumThresh), \
								 'contractPixels':str(self.env.contractPixels), \
								 'cloudFilter':str(self.env.metadataCloudCoverMax),\
								 'crs':str(self.env.epsg), \
								 'dilatePixels':str(self.env.dilatePixels)})

		return img

	def exportMap(self,img,studyArea,week):

		geom  = studyArea.geometry().bounds().getInfo();
		
		task_ordered= ee.batch.Export.image.toAsset(image=img.clip(studyArea), 
								  description = self.env.name + str(week), 
								  assetId= self.env.assetId + self.env.name + str(week).zfill(3),
								  region=geom['coordinates'], 
								  maxPixels=1e13,
								  crs=self.env.epsg,
								  scale=self.env.exportScale)
	
		task_ordered.start() 



if __name__ == "__main__":        

	ee.Initialize()
	
	studyArea = ee.FeatureCollection("users/apoortinga/countries/Ecuador_nxprovincias") #.geometry() #.bounds();


	# 2015
	year = ee.Date("2016-01-01")
	startWeek = 39
	startDay = [168,182,196,210,224,238,252,266,280,294,308,322,336,350,364]
	endDay =   [181,195,209,223,237,251,265,279,293,307,321,335,349,363,377]

	# 2016
	year = ee.Date("2016-01-01")
	startWeek = 54
	startDay = [13,27,41,55,69,83,97,111,125,139,153,167,181,195,209,223,237,251,265,279,293,307,321,335,349,363]
	endDay = [26,40,54,68,82,96,110,124,138,152,166,180,194,208,222,236,250,264,278,292,306,320,334,348,362,376]

 	# 2017
	year = ee.Date("2017-01-01")
	startWeek = 80
	startDay = [11,25,39,53,67,81,95,109,123,137,151,165,179,193,207,221,235,249,263,277,291,305,319,333,347,361]
	endDay = [24,38,52,66,80,94,108,122,136,150,164,178,192,206,220,234,248,262,276,290,304,318,332,346,360,374]

	# 2018
	year = ee.Date("2018-01-01")
	startWeek = 106
	startDay = [10,24,38,52,66,80,94,108,122,136,150,164,178,192,206,220,234,248,262,276,290,304,318,332,346,360]
	endDay = [23,37,51,65,79,93,107,121,135,149,163,177,191,205,219,233,247,261,275,289,303,317,331,345,359,373]
	
	
	for i in range(2,3,1):
		startDate = year.advance(startDay[i],"day")
		endDate = year.advance(endDay[i],"day")
		
		functions().main(studyArea,startDate,endDate,startDay[i],endDay[i],startWeek+i)
