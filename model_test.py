import numpy as np
import pickle

model_object = pickle.load(open('model/model.pkl', 'rb'))
model = model_object['model']

FEATURES = model_object['features']

neon_row_data =[{
  "price_area": "SE3",
  "target_date": "2026-09-21",
  "tomorrow_temp_mean": 8.775,
  "tomorrow_temp_min": 7.1,
  "tomorrow_temp_max": 12.2,
  "tomorrow_wind_mean": 2.1666666666666665,
  "price_today": 0.09852135416666667,
  "price_yesterday": 0.04344833333333333,
  "price_7d_ago": 0.7495031249999999,
  "price_7d_mean": 0.6041904761904763,
  "peak_today": 0.46878,
  "dayofweek": 6,
  "month": 9,
  "is_weekend": True,
  "y": 0.5632847916666667,
  "built_at": "2026-09-21 10:56:43.339687+00"
}]

neon_row_data = neon_row_data[0]
feature_data = [neon_row_data[feature] for feature in FEATURES]
feature_data = np.array(feature_data).reshape(1, -1)

prediction = model.predict(feature_data)
print(f'Prediction: {prediction[0]}')