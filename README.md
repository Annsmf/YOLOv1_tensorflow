# YOLOv1_tensorflow
YOLOv1 tensorflow
####for very_tiny_yolov2:
(1)  reduce the lr decay from train_size(122) to 10000    

(2)  modify the loss function from (sqrt(w)-sqrt(w')) to (w/w'-1) because the gradient of (w/w') is better than the       gradient of (sqrt(w))， if the bbox is a small object but due to a weight initialization w may be not a small value so the gradient of (sqrt(w))=1/2*(1/sqrt(w)) will less than 1, equivalent to the assignment of a small weight to w, this is not consistent with our original intention give more attention to w and h      


(3)  add position information into original RGB img
