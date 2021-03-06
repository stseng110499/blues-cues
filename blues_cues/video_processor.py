import os
import pyautogui
import cv2
import pytesseract  # for reading text
import time
import numpy as np
import requests
import imutils
from Quartz import CGWindowListCopyWindowInfo, kCGNullWindowID, kCGWindowListOptionAll
from PIL import ImageGrab
import sys

ZOOM = 'Zoom Meeting'
API_KEY = '385d384953a846119ef795da65630382'
ENDPOINT = 'https://bc-faces.cognitiveservices.azure.com'
UPDATE_TIME_SECS = 2

class VideoProcessor():
	def __init__(self):
		self.update_zoom_window_info()
		self.image = None
		self.prev_image = None
		self.est_width, self.est_height = None, None

	def update_zoom_window_info(self):
		window_list = CGWindowListCopyWindowInfo(kCGWindowListOptionAll, kCGNullWindowID)
		for i in range(len(window_list)):
			window = window_list[i]
			try:
				if ZOOM in window['kCGWindowName']:
					self.zoom_window_info = (i, window['kCGWindowBounds'])
					break
			except:
				pass
		else: 
			# if we reach the end of the loop, no Zoom window
			raise Exception("Could not find Zoom window.")
	

	def screenshot_zoom(self, debug=False):
		id, bounds = self.zoom_window_info
		window = CGWindowListCopyWindowInfo(kCGWindowListOptionAll, kCGNullWindowID)[id]
		if 'kCGWindowName' not in window or ZOOM not in window['kCGWindowName']:
			# Window ID has changed (or closed), find it again
			self.update_zoom_window_info()

		# multiply everything by 2 because of Mac pixel doubling
		x = 2*bounds['X']
		y = 2*bounds['Y']
		width = 2*bounds['Width']
		height = 2*bounds['Height']

		bbox = (x, y, x + 2*width, y + 2*height)
		img = ImageGrab.grab(bbox)
		img = np.array(img)
		img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
		if debug:
			cv2.imwrite('screenshot.jpg', img)
			
		return img

	def estimate_panel_size(self, img, debug=False):
		"""
		Uses edge detection / hough lines to estimate the size of a single
		video panel. 

		Returns (estimated width, estimated height).
		"""
		# binarize
		gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
		binary = cv2.bitwise_not(gray) 
		output = np.zeros(img.shape, img.dtype)
		# detect edges
		edges = cv2.Canny(binary, 100, 200, output)
		if debug:
			cv2.imwrite('edges.jpg', edges) 
		lines = cv2.HoughLinesP(edges, 1, np.pi/180, 200)   # horizontal/vertical lines
		# store coords of lines
		a,b,c = lines.shape
		x_vals = set()
		y_vals = set()
		for i in range(a):
			x1, y1 = lines[i][0][:2]
			x2, y2 = lines[i][0][2:4]
			x_vals.add(x1)
			x_vals.add(x2)
			y_vals.add(y1)
			y_vals.add(y2)
		# estimate width and height of one panel from lines
		MIN_HEIGHT = img.shape[0] // 7
		MIN_WIDTH = img.shape[1] // 7
		est_height = None
		est_width = None
		for x1 in x_vals:
			for x2 in x_vals:
				diff = abs(x1-x2)
				if diff > MIN_WIDTH and (est_width is None or diff < est_width):
					est_width = diff

		for y1 in y_vals:
			for y2 in y_vals:
				diff = abs(y1-y2)
				if diff > MIN_HEIGHT and (est_height is None or diff < est_height):
					est_height = diff
		if debug:
			print("(W, H) = ({}, {})".format(est_width, est_height))
		
		if est_height is None or est_width is None:
			return img.shape[1], img.shape[0]

		return (est_width, est_height)

	# def count_muted(img, debug=False):
	# 	"""
	# 	Count the number of muted symbols in this image. 
	# 	"""
	# 	muted = cv2.imread('../muted_symbol.png')
	# 	w, h = muted.shape[:2]

	# 	found = set()
	# 	threshold_dist = min(img.shape[0], img.shape[1]) // 7
	# 	for scale in np.linspace(0.2, 1.0, 20)[::-1]:
	# 		# resize the image according to the scale, and keep track
	# 		# of the ratio of the resizing
	# 		resized = imutils.resize(img, width = int(img.shape[1] * scale))
	# 		r = img.shape[1] / float(resized.shape[1])
	# 		# if the resized image is smaller than the template, then break
	# 		# from the loop
	# 		if resized.shape[0] < w or resized.shape[1] < h:
	# 			break

	# 		res = cv2.matchTemplate(img, muted, cv2.TM_CCOEFF_NORMED)
	# 		threshold = 0.56
	# 		loc = np.where(res >= threshold)

	# 		for pt in zip(*loc[::-1]):
	# 			cv2.rectangle(img, pt, (pt[0] + w, pt[1] + h), (0, 255, 0), 2)
	# 			found.add((pt[0], pt[1]))


	# 	if debug:
	# 		cv2.imwrite('matched.png', img)
		
	# 	return len(found)

	def estimate_camera_on_attendance(self, img1, img2, debug=False):
		"""
		Estimate the number of people who have their camera on.
		img1 and img2 are two cropped images showing just the zoom gallery view,
			and must be the same size. 

		Uses edge detection / hough lines to estimate the size of one person's
		video panel, then uses absolute diff and finds bounding boxes to estimate
		the number of panels that are in motion
		"""
		if (img1.shape[:2] != img2.shape[:2]):
			return

		if self.est_width is None or self.est_height is None:
			self.est_width, self.est_height = self.estimate_panel_size(img1)
		
		# get absolute diff between images
		diff = img1.copy()
		cv2.absdiff(img1, img2, diff)
		gray = cv2.cvtColor(diff, cv2.COLOR_BGR2GRAY)
		for i in range(0, 3):
			dilated = cv2.dilate(gray.copy(), None, iterations= i+ 1)
		(T, threshold) = cv2.threshold(gray, 3, 255, cv2.THRESH_BINARY)
		if debug:
			cv2.imwrite("diff.png", threshold)

		# fallback naive estimate: just uses proportion of different pixels 
		naive_estimate = cv2.countNonZero(gray) / (gray.shape[0] * gray.shape[1])
		if debug:
			print("naive estimate: {}".format(naive_estimate))

		# get bounding rectangles from diff
		(contours, hierarchy) = cv2.findContours(threshold, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
		rectangles = list(map(cv2.boundingRect, contours))
		count = 0
		for x, y, w, h in rectangles:
			if w*h < self.est_width*self.est_height:
				continue
			count += (w*h) // (self.est_width*self.est_height)
			if debug:
				cv2.rectangle(img2, (x,y), (x+w,y+h), (0, 255, 0), 10)
		if debug:
			cv2.imwrite('rectangles.jpg', img2)
		total = (img1.shape[1] // self.est_height) * (img1.shape[0] // self.est_width)
		
		if count == 0:
			return naive_estimate

		return min(count / total, 1)


	def azure_request(self, img, API_KEY, ENDPOINT):
		PATH_TO_API = '/face/v1.0/detect'

		params = {
		    'returnFaceId': 'true',
		    'returnFaceLandmarks': 'false',
		    'returnFaceAttributes': 'age,gender,headPose,smile,emotion',
		}

		headers = {'Ocp-Apim-Subscription-Key': API_KEY, 
				   'Content-Type': 'application/octet-stream'}

		is_success, img_encoded = cv2.imencode(".png", img)

		response = requests.post(ENDPOINT + PATH_TO_API, data=img_encoded.tobytes(), params=params, headers=headers)
		output = response.json()

		return output

	def emotion_analysis(self, individual_emotions):
		total_emotions = {}
		emotion_keys = individual_emotions[0].keys()
		for emotion in emotion_keys:
			total_emotions[emotion] = sum([d[emotion] for d in individual_emotions]) / len(individual_emotions)

		return total_emotions

	def face_analysis(self, img):
		output = self.azure_request(img, API_KEY, ENDPOINT)
		average_age = 0
		gender = {"male": 0, "female": 0}
		smile = {"Yes": 0, "No": 0}
		num_faces = len(output)
		looking_away = 0
		emotions = []

		for face in output:
			average_age += face['faceAttributes']['age'] / num_faces
			gender[face['faceAttributes']['gender']] += 1
			if face['faceAttributes']['smile'] > 0.5:
				smile["Yes"] += 1
			else:
				smile["No"] += 1

			if abs(face['faceAttributes']['headPose']['pitch']) > 15 or abs(face['faceAttributes']['headPose']['yaw']) > 15:
				looking_away += 1 / num_faces

			emotions.append(face['faceAttributes']['emotion'])

		if len(emotions) == 0:
			emotions = [{"anger": 0, "contempt": 0, "disgust": 0, "fear": 0, "happiness": 0, "neutral": 0, "sadness": 0, "surprise": 0}]

		return round(average_age), gender, smile, looking_away, self.emotion_analysis(emotions)

	def run(self, queue):
		while True:
			print("video processor iteration")
			self.prev_image = self.image
			self.image = self.screenshot_zoom()
			if self.prev_image is not None:
				attendance = self.estimate_camera_on_attendance(self.prev_image, self.image)

				age, gender, smile, lookaway, emotions = self.face_analysis(self.image)

				title = "Meeting Demographics"
				content = ("Average Age: {}\nGender Distribution: {} M, {} F\nParticipants Smiling: {}, "
				"Participants Not Smiling: {}").format(age, gender["male"], gender["female"], smile["Yes"], smile["No"])
				queue.put((title, content))

				title = "Meeting Sentiment"
				content = ("Anger: {:.1f}%\nContempt: {:.1f}%\nDisgust: {:.1f}%\nFear: {:.1f}%\nHappiness: {:.1f}%\n"
				"Neutral: {:.1f}%\nSadness: {:.1f}%\nSurprise: {:.1f}%").format(
					emotions['anger']*100, emotions['contempt']*100, emotions['disgust']*100, emotions['fear']*100, \
					emotions['happiness']*100, emotions['neutral']*100, emotions['sadness']*100, emotions['surprise']*100)
				queue.put((title, content))

				title = "Audience Engagement"
				content = ("Participants Looking Away: {:.1f}%\nCameras On: {:.1f}%").format(lookaway*100, attendance*100)
				queue.put((title, content))

			time.sleep(UPDATE_TIME_SECS)



if __name__ == "__main__":
	vp = VideoProcessor()
	# vp.run()

#### EAST text detector
## Code inspired by:
## https://www.pyimagesearch.com/2018/08/20/opencv-text-detection-east-text-detector/

# resize to multiple of 32
# (newW, newH) = (320,320)
# image = cv2.resize(img, (newW, newH))
# (H, W) = img.shape[:2]

# layers = [
# 	"feature_fusion/Conv_7/Sigmoid",    # confidence scores
# 	"feature_fusion/concat_3",          # bounding box
# ]

# net = cv2.dnn.readNet("frozen_east_text_detection.pb")
# blob = cv2.dnn.blobFromImage(img, 1.0, (W, H),
# 	(123.68, 116.78, 103.94), swapRB=True, crop=False)
# net.setInput(blob)
# (scores, geometry) = net.forward(layers)

# (numRows, numCols) = scores.shape[2:4]
# rects = []
# confidences = []
# for y in range(0, numRows):
# 	# extract the scores (probabilities), followed by the geometrical
# 	# data used to derive potential bounding box coordinates that
# 	# surround text
# 	scoresData = scores[0, 0, y]
# 	xData0 = geometry[0, 0, y]
# 	xData1 = geometry[0, 1, y]
# 	xData2 = geometry[0, 2, y]
# 	xData3 = geometry[0, 3, y]
# 	anglesData = geometry[0, 4, y]