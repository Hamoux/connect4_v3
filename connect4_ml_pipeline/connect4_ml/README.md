deja pour que contre notre ia il faut charge ia_modele_bridge depuis backup(changer entre les deux fichier prsq maintenant ia_modele bridge joue avec minimax+ia plus fort)




pour faire des donnees utilisant deux datasets pour avoir max de diversité 
python .\merge_datasets.py --dataset-a .\ml_dataset\train.npz --dataset-b .\selfplay_data\selfplay_v1.npz --weight-a 80 --weight-b 20 --total-samples 3000000 --output .\merged_data\train_80_20.npz --save-meta


pour lancer le tournoi de Ia old vs iA nouveau (si t'as un bon carte graphique changer le device a gpu mais il fallait installer pytorche voir avec chatgpt il va t'expliquer) :  
python .\arena.py --model-a .\runs\cpu_test\best_model.pt --model-b .\runs\cpu_test_v2\best_model.pt --games 500 --device cpu --log-every 25 --save-json .\arena_reports\v1_vs_v2.json


extracter la dataset 
python .\extract_dataset_all_skip_26k_51k.py --host localhost --port 5432 --dbname Connect4DB --user postgres --password YOUR_PASSWORD --rows 9 --cols 9 --min-moves 6 --output-dir .\ml_dataset --dedupe-signatures

trainer le modele 
python .\train_verbose_fast.py --data-dir .\ml_dataset --epochs 3 --batch-size 256 --num-workers 4 --log-every 50 --output-dir .\runs\cpu_test

evaluer le modele 
python .\evaluate.py --data-dir .\ml_dataset --model .\runs\cpu_test\best_model.pt --batch-size 512 --device cpu

evaluer bien 
python .\evaluate_verbose.py --data-dir .\ml_dataset --model .\runs\cpu_test\best_model.pt --batch-size 256 --device cpu --log-every 20

best modele selfplay 
python .\self_play.py --model .\runs\cpu_test\best_model.pt --games 2000 --output-dir .\selfplay_data --save-prefix selfplay_v1 --log-every 50 --save-games-jsonl


