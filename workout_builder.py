import logging

logger = logging.getLogger(__name__)

def build_garmin_workout(parsed_json):
    """
    Преобразует простой словарь от Gemini в сложную структуру Garmin JSON,
    опираясь на логику правильных DTO-шагов.
    """
# Внутренние идентификаторы Garmin для бега
    running_sport = {"sportTypeId": 1, "sportTypeKey": "running"}
    
    workout = {
        "workoutName": parsed_json.get("workoutName", "Тренировка"),
        "sportType": running_sport,
        "workoutSegments": [
            {
                "segmentOrder": 1, 
                "sportType": running_sport, 
                "workoutSteps": []
            }
        ]
    }
    
    step_order = 1
    garmin_steps = workout["workoutSegments"][0]["workoutSteps"]
    
    def create_step(step_data, order):
        # Тип шага (разминка, заминка, активный, восстановление)
        type_map = {
            "warmup": (1, "warmup"), 
            "cooldown": (2, "cooldown"), 
            "active": (3, "active"), 
            "recovery": (4, "recovery")
        }
        step_type_id, step_type_key = type_map.get(step_data.get("type", "active"), (3, "active"))
        
        # Условие завершения (время, дистанция, нажатие кнопки Lap)
        dur_map = {
            "time": (2, "time"), 
            "distance": (3, "distance"), 
            "lap": (1, "lap.button")
        }
        cond_id, cond_key = dur_map.get(step_data.get("duration_type", "lap"), (1, "lap.button"))
        
        step = {
            "type": "ExecutableStepDTO",
            "stepOrder": order,
            "stepType": {"stepTypeId": step_type_id, "stepTypeKey": step_type_key},
            "endCondition": {"conditionTypeId": cond_id, "conditionTypeKey": cond_key},
            "endConditionValue": step_data.get("duration_value"),
            "targetType": {"workoutTargetTypeId": 1, "workoutTargetTypeKey": "no.target"},
            "targetValueOne": None,
            "targetValueTwo": None
        }
        
        # Цели (пульс или темп)
        t_type = step_data.get("target_type")
        if t_type == "hr":
            step["targetType"] = {"workoutTargetTypeId": 4, "workoutTargetTypeKey": "heart.rate.bpm"}
            step["targetValueOne"] = step_data.get("target_low")
            step["targetValueTwo"] = step_data.get("target_high")
        elif t_type == "pace":
            step["targetType"] = {"workoutTargetTypeId": 6, "workoutTargetTypeKey": "pace.zone"}
            # Garmin использует метры в секунду (м/с) для темпа. 
            # Формула: 1000 / (секунды на километр)
            sec_low = step_data.get("target_low_sec")
            sec_high = step_data.get("target_high_sec")
            if sec_high: 
                step["targetValueOne"] = round(1000.0 / sec_high, 3)
            if sec_low: 
                step["targetValueTwo"] = round(1000.0 / sec_low, 3)
            
        return step

    # Обработка шагов и интервалов
    for step_data in parsed_json.get("steps", []):
        if step_data.get("type") == "repeat":
            repeat_step = {
                "type": "RepeatGroupDTO",
                "stepOrder": step_order,
                "stepType": {"stepTypeId": 6, "stepTypeKey": "repeat"},
                "numberOfIterations": step_data.get("iterations", 1),
                "workoutSteps": [],
                "smartRepeat": False
            }
            step_order += 1
            
            # Вложенные шаги внутри интервала
            for sub_step in step_data.get("steps", []):
                repeat_step["workoutSteps"].append(create_step(sub_step, step_order))
                step_order += 1
                
            garmin_steps.append(repeat_step)
        else:
            garmin_steps.append(create_step(step_data, step_order))
            step_order += 1
            
    return workout
